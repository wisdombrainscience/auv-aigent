from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import fcntl
import os
from typing import Iterator

from ai.artifacts.schema import ArtifactInputVersion, ArtifactRecord
from ai.artifacts.versions import (
    ArtifactVersionConflictError,
    ArtifactVersionNotFoundError,
    ArtifactVersionStore,
)
from ai.contracts.artifact_states import ArtifactState
from ai.contracts.ids import CanonicalId, CanonicalIdError, IdKind
from ai.contracts.task_states import TaskState
from ai.project.store import ProjectNotFoundError, ProjectStore, ProjectStoreConflictError
from ai.review.role import REVIEWER_ROLE_ID, bind_reviewer_role
from ai.tasks.schema import MAX_TASK_ATTEMPTS, TaskRecord
from ai.tasks.store import (
    TaskCreateInput,
    TaskNotFoundError,
    TaskStore,
    TaskStoreConflictError,
)


REVIEW_TASK_GENERATION_CONTRACT_VERSION = "1.0"
REVIEW_TASK_OUTPUT_SCHEMA = "review_result.v1"
REVIEW_TASK_TITLE_PREFIX = "Review"
REVIEW_TASK_LOCK_FILENAME = ".review-task-generation.lock"
MAX_REVIEW_TASK_SCAN = 4096


class ReviewTaskGenerationError(RuntimeError):
    """Base error for bounded R9.3 review-task generation."""


class ReviewTaskGenerationConflictError(ReviewTaskGenerationError):
    """Raised when a review task cannot be generated safely."""


@dataclass(frozen=True)
class ReviewTaskGenerationResult:
    """One persisted reviewer TaskRecord bound to exactly one artifact version."""

    artifact: ArtifactRecord
    task: TaskRecord
    reused_existing: bool

    def __post_init__(self) -> None:
        if not isinstance(self.artifact, ArtifactRecord):
            raise TypeError("artifact must be ArtifactRecord")
        if not isinstance(self.task, TaskRecord):
            raise TypeError("task must be TaskRecord")
        if not isinstance(self.reused_existing, bool):
            raise TypeError("reused_existing must be boolean")


def _project_id(raw: CanonicalId | str) -> CanonicalId:
    try:
        value = raw if isinstance(raw, CanonicalId) else CanonicalId.parse(raw)
    except (CanonicalIdError, TypeError) as exc:
        raise ReviewTaskGenerationConflictError("project_id must be canonical") from exc
    if value.kind is not IdKind.PROJECT:
        raise ReviewTaskGenerationConflictError("project_id must identify a project")
    return value


class ReviewTaskGenerator:
    """Generate one persisted reviewer task for one DRAFT artifact version.

    R9.3 owns task generation only. It does not execute the task, transition the
    artifact to REVIEWED, allocate/persist ReviewIssue records, route issues,
    create rework, enforce rework bounds, or suppress issue duplicates.

    The target producer task must have ``review_required=True``. Generated
    review tasks deliberately set ``review_required=False`` to avoid recursive
    review-task generation. The task depends on the source artifact's producer
    task and references the exact immutable artifact version as input.
    """

    def __init__(self, project_store: ProjectStore) -> None:
        if not isinstance(project_store, ProjectStore):
            raise TypeError("project_store must be ProjectStore")
        self._projects = project_store
        self._tasks = TaskStore(project_store)
        self._artifacts = ArtifactVersionStore(project_store)
        bind_reviewer_role()  # fail closed if frozen reviewer role is unavailable

    @contextmanager
    def _lock(self, project_id: CanonicalId) -> Iterator[None]:
        try:
            handle = self._projects.open_project(project_id)
        except (ProjectNotFoundError, ProjectStoreConflictError) as exc:
            raise ReviewTaskGenerationConflictError(
                f"project could not be opened: {exc}"
            ) from exc
        lock_path = handle.layout.data_directories["tasks"] / REVIEW_TASK_LOCK_FILENAME
        if lock_path.is_symlink():
            raise ReviewTaskGenerationConflictError("review task lock must not be a symlink")
        if lock_path.exists() and not lock_path.is_file():
            raise ReviewTaskGenerationConflictError("review task lock must be a regular file")
        try:
            fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
        except OSError as exc:
            raise ReviewTaskGenerationConflictError("review task lock could not be opened") from exc
        try:
            os.chmod(lock_path, 0o600)
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        except OSError as exc:
            raise ReviewTaskGenerationConflictError("review task lock failed") from exc
        finally:
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            finally:
                os.close(fd)

    @staticmethod
    def _target_ref(artifact: ArtifactRecord) -> ArtifactInputVersion:
        return ArtifactInputVersion(entity_id=artifact.artifact_id, version=artifact.version)

    @staticmethod
    def _title(artifact: ArtifactRecord) -> str:
        return f"{REVIEW_TASK_TITLE_PREFIX} {artifact.artifact_id.value}@v{artifact.version}"

    def _find_existing(
        self,
        project_id: CanonicalId,
        artifact: ArtifactRecord,
        producer: TaskRecord,
    ) -> TaskRecord | None:
        records = self._tasks.list_tasks(project_id)
        if len(records) > MAX_REVIEW_TASK_SCAN:
            raise ReviewTaskGenerationConflictError(
                "review task recovery exceeds MAX_REVIEW_TASK_SCAN"
            )
        target = self._target_ref(artifact)
        matching: list[TaskRecord] = []
        ambiguous: list[TaskRecord] = []
        for record in records:
            if record.role != REVIEWER_ROLE_ID or target not in record.input_artifacts:
                continue
            exact = (
                record.title == self._title(artifact)
                and record.dependencies == (producer.task_id,)
                and record.input_artifacts == (target,)
                and record.output_schema == REVIEW_TASK_OUTPUT_SCHEMA
                and record.validators == ()
                and record.review_required is False
                and record.max_attempts == MAX_TASK_ATTEMPTS
                and record.priority == producer.priority
            )
            (matching if exact else ambiguous).append(record)
        if ambiguous:
            raise ReviewTaskGenerationConflictError(
                "conflicting reviewer task already references target artifact version"
            )
        if len(matching) > 1:
            raise ReviewTaskGenerationConflictError(
                "multiple R9.3 review tasks reference one artifact version"
            )
        return matching[0] if matching else None

    def generate(
        self,
        project_id: CanonicalId | str,
        artifact_id: CanonicalId | str,
        version: int,
    ) -> ReviewTaskGenerationResult:
        project = _project_id(project_id)
        try:
            artifact = self._artifacts.open_version(project, artifact_id, version)
        except (ArtifactVersionNotFoundError, ArtifactVersionConflictError) as exc:
            raise ReviewTaskGenerationConflictError(
                f"target artifact cannot be opened safely: {exc}"
            ) from exc
        try:
            producer = self._tasks.open_task(project, artifact.producer_task)
        except (TaskNotFoundError, TaskStoreConflictError) as exc:
            raise ReviewTaskGenerationConflictError(
                f"artifact producer task cannot be opened safely: {exc}"
            ) from exc
        if not producer.review_required:
            raise ReviewTaskGenerationConflictError(
                "artifact producer task does not require review"
            )

        with self._lock(project):
            existing = self._find_existing(project, artifact, producer)
            if existing is not None:
                return ReviewTaskGenerationResult(
                    artifact=artifact,
                    task=existing,
                    reused_existing=True,
                )
            if artifact.status is not ArtifactState.DRAFT:
                raise ReviewTaskGenerationConflictError(
                    "new review tasks may be generated only for DRAFT artifact versions"
                )
            try:
                task = self._tasks.create_task(
                    project,
                    TaskCreateInput(
                        title=self._title(artifact),
                        role=REVIEWER_ROLE_ID,
                        priority=producer.priority,
                        dependencies=(producer.task_id,),
                        input_artifacts=(self._target_ref(artifact),),
                        output_schema=REVIEW_TASK_OUTPUT_SCHEMA,
                        validators=(),
                        review_required=False,
                        max_attempts=MAX_TASK_ATTEMPTS,
                    ),
                )
            except TaskStoreConflictError as exc:
                raise ReviewTaskGenerationConflictError(
                    f"review task could not be persisted safely: {exc}"
                ) from exc
            if task.status is not TaskState.CREATED or task.attempt != 0:
                raise ReviewTaskGenerationConflictError(
                    "persisted review task did not start in CREATED/attempt=0"
                )
            return ReviewTaskGenerationResult(
                artifact=artifact,
                task=task,
                reused_existing=False,
            )
