from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import fcntl
import hashlib
import os
import re
from typing import Iterator

from ai.artifacts.schema import ArtifactInputVersion
from ai.contracts.ids import CanonicalId, CanonicalIdError, IdKind
from ai.contracts.review_result import ReviewIssue
from ai.contracts.task_states import TaskState
from ai.project.store import ProjectNotFoundError, ProjectStore, ProjectStoreConflictError
from ai.review.routing import (
    IssueOwnerRoute,
    IssueOwnerRouter,
    IssueOwnerRoutingError,
)
from ai.tasks.schema import TaskRecord
from ai.tasks.store import (
    TaskCreateInput,
    TaskNotFoundError,
    TaskStore,
    TaskStoreConflictError,
)


REWORK_TASK_GENERATION_CONTRACT_VERSION = "1.0"
REWORK_TASK_TITLE_PREFIX = "Rework"
REWORK_TASK_LOCK_FILENAME = ".rework-task-generation.lock"
MAX_REWORK_TASK_SCAN = 4096

# Frozen RUAV-AI-ARCH-V8.0-BL1 review anti-loop limit. This is deliberately
# separate from TaskRecord.max_attempts=3 (per-task execution retry budget).
MAX_REWORK_GATE_CONTRACT_VERSION = "1.0"
MAX_REWORK_ATTEMPTS = 2
DUPLICATE_ISSUE_SUPPRESSION_CONTRACT_VERSION = "1.0"
_REWORK_TITLE_RE = re.compile(
    r"^Rework (ISSUE-[0-9]{4}) ([0-9a-f]{64}) for "
    r"(ART-[0-9]{6})@v([1-9][0-9]*)$"
)


class ReworkTaskGenerationError(RuntimeError):
    """Base error for bounded R9.5 rework-task generation."""


class ReworkTaskGenerationConflictError(ReworkTaskGenerationError):
    """Raised when a review issue cannot be materialized as one safe rework task."""


class MaxReworkGateExceeded(ReworkTaskGenerationConflictError):
    """Raised before a third rework task can be created for one review lineage."""

    def __init__(self, decision: "MaxReworkGateDecision") -> None:
        if not isinstance(decision, MaxReworkGateDecision):
            raise TypeError("decision must be MaxReworkGateDecision")
        if decision.allowed or decision.target_status is not TaskState.NEEDS_HUMAN:
            raise ValueError("exceeded decision must route to NEEDS_HUMAN")
        self.decision = decision
        super().__init__(
            "review rework budget exhausted; automation must stop at NEEDS_HUMAN"
        )


@dataclass(frozen=True)
class DuplicateIssueSuppressionDecision:
    """Immutable R9.7 decision for one fingerprint inside one review lineage."""

    project_id: CanonicalId
    review_task_id: CanonicalId
    target_artifact: ArtifactInputVersion
    fingerprint_digest: str
    duplicate: bool
    existing_issue_id: CanonicalId | None
    existing_task_id: CanonicalId | None

    def __post_init__(self) -> None:
        if not isinstance(self.project_id, CanonicalId) or self.project_id.kind is not IdKind.PROJECT:
            raise ReworkTaskGenerationConflictError(
                "suppression project_id must identify a project"
            )
        if not isinstance(self.review_task_id, CanonicalId) or self.review_task_id.kind is not IdKind.TASK:
            raise ReworkTaskGenerationConflictError(
                "suppression review_task_id must identify a task"
            )
        if not isinstance(self.target_artifact, ArtifactInputVersion):
            raise ReworkTaskGenerationConflictError(
                "suppression target_artifact must be ArtifactInputVersion"
            )
        if not isinstance(self.fingerprint_digest, str) or re.fullmatch(
            r"[0-9a-f]{64}", self.fingerprint_digest
        ) is None:
            raise ReworkTaskGenerationConflictError(
                "suppression fingerprint_digest must be lowercase sha256"
            )
        if not isinstance(self.duplicate, bool):
            raise ReworkTaskGenerationConflictError(
                "suppression duplicate must be boolean"
            )
        if self.duplicate:
            if (
                not isinstance(self.existing_issue_id, CanonicalId)
                or self.existing_issue_id.kind is not IdKind.ISSUE
            ):
                raise ReworkTaskGenerationConflictError(
                    "duplicate suppression requires existing ISSUE id"
                )
            if (
                not isinstance(self.existing_task_id, CanonicalId)
                or self.existing_task_id.kind is not IdKind.TASK
            ):
                raise ReworkTaskGenerationConflictError(
                    "duplicate suppression requires existing TASK id"
                )
        elif self.existing_issue_id is not None or self.existing_task_id is not None:
            raise ReworkTaskGenerationConflictError(
                "non-duplicate suppression decision must not reference existing records"
            )


@dataclass(frozen=True)
class MaxReworkGateDecision:
    """Immutable R9.6 decision for one exact review/rework lineage."""

    project_id: CanonicalId
    review_task_id: CanonicalId
    target_artifact: ArtifactInputVersion
    rework_attempts: int
    max_rework_attempts: int
    allowed: bool
    target_status: TaskState | None

    def __post_init__(self) -> None:
        if not isinstance(self.project_id, CanonicalId) or self.project_id.kind is not IdKind.PROJECT:
            raise ReworkTaskGenerationConflictError("gate project_id must identify a project")
        if not isinstance(self.review_task_id, CanonicalId) or self.review_task_id.kind is not IdKind.TASK:
            raise ReworkTaskGenerationConflictError("gate review_task_id must identify a task")
        if not isinstance(self.target_artifact, ArtifactInputVersion):
            raise ReworkTaskGenerationConflictError(
                "gate target_artifact must be ArtifactInputVersion"
            )
        for field in ("rework_attempts", "max_rework_attempts"):
            value = getattr(self, field)
            if isinstance(value, bool) or not isinstance(value, int):
                raise ReworkTaskGenerationConflictError(f"gate {field} must be an integer")
        if self.max_rework_attempts != MAX_REWORK_ATTEMPTS:
            raise ReworkTaskGenerationConflictError("gate max_rework_attempts must match frozen limit")
        if not 0 <= self.rework_attempts <= self.max_rework_attempts:
            raise ReworkTaskGenerationConflictError("gate rework_attempts is outside bounded range")
        if not isinstance(self.allowed, bool):
            raise ReworkTaskGenerationConflictError("gate allowed must be boolean")
        expected_allowed = self.rework_attempts < self.max_rework_attempts
        if self.allowed is not expected_allowed:
            raise ReworkTaskGenerationConflictError("gate allowed flag is inconsistent")
        expected_target = None if expected_allowed else TaskState.NEEDS_HUMAN
        if self.target_status is not expected_target:
            raise ReworkTaskGenerationConflictError("gate target status is inconsistent")


@dataclass(frozen=True)
class ReworkTaskGenerationResult:
    """One persisted task bound to one routed review issue and artifact version."""

    route: IssueOwnerRoute
    task: TaskRecord
    reused_existing: bool
    suppressed_duplicate: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.route, IssueOwnerRoute):
            raise TypeError("route must be IssueOwnerRoute")
        if not isinstance(self.task, TaskRecord):
            raise TypeError("task must be TaskRecord")
        if not isinstance(self.reused_existing, bool):
            raise TypeError("reused_existing must be boolean")
        if not isinstance(self.suppressed_duplicate, bool):
            raise TypeError("suppressed_duplicate must be boolean")
        if self.suppressed_duplicate and not self.reused_existing:
            raise ValueError("suppressed duplicate must reuse an existing task")


def _project_id(raw: CanonicalId | str) -> CanonicalId:
    try:
        value = raw if isinstance(raw, CanonicalId) else CanonicalId.parse(raw)
    except (CanonicalIdError, TypeError) as exc:
        raise ReworkTaskGenerationConflictError("project_id must be canonical") from exc
    if value.kind is not IdKind.PROJECT:
        raise ReworkTaskGenerationConflictError("project_id must identify a project")
    return value


def _fingerprint_digest(issue: ReviewIssue) -> str:
    return hashlib.sha256(issue.fingerprint.encode("utf-8")).hexdigest()


class MaxReworkGate:
    """Evaluate the frozen two-attempt review rework budget without writes.

    The lineage key is persisted provenance, not AI-authored issue semantics:
    one exact R9.3 review task + exact reviewed artifact + routed owner execution
    contract. Existing R9.5 rework tasks in that lineage consume the bounded
    review budget. Different issue IDs/fingerprints still count as attempts;
    cross-issue duplicate-fingerprint suppression remains R9.7.
    """

    def __init__(self, project_store: ProjectStore) -> None:
        if not isinstance(project_store, ProjectStore):
            raise TypeError("project_store must be ProjectStore")
        self._tasks = TaskStore(project_store)
        self._router = IssueOwnerRouter(project_store)

    @staticmethod
    def _is_r9_5_lineage_task(
        record: TaskRecord,
        route: IssueOwnerRoute,
        owner: TaskRecord,
    ) -> bool:
        if not record.title.startswith(f"{REWORK_TASK_TITLE_PREFIX} "):
            return False

        match = _REWORK_TITLE_RE.fullmatch(record.title)
        if match is None:
            # A task claiming the reserved R9.5 title prefix must never be
            # silently ignored by the anti-loop gate.
            raise ReworkTaskGenerationConflictError(
                "malformed R9.5 rework task title encountered"
            )

        try:
            issue_id = CanonicalId.parse(match.group(1))
        except CanonicalIdError as exc:
            raise ReworkTaskGenerationConflictError(
                "R9.5 rework title contains non-canonical issue_id"
            ) from exc
        if issue_id.kind is not IdKind.ISSUE:
            raise ReworkTaskGenerationConflictError(
                "R9.5 rework title issue_id has wrong canonical kind"
            )

        artifact_id, version = match.group(3), int(match.group(4))
        is_target = (
            artifact_id == route.target_artifact.entity_id.value
            and version == route.target_artifact.version
        )
        if not is_target:
            return False

        if not (
            record.role == route.owner_role
            and record.priority == owner.priority
            and record.dependencies == (route.review_task_id,)
            and record.input_artifacts == (route.target_artifact,)
            and record.output_schema == owner.output_schema
            and record.validators == owner.validators
            and record.review_required is owner.review_required
            and record.max_attempts == owner.max_attempts
        ):
            raise ReworkTaskGenerationConflictError(
                "R9.5 rework task conflicts with persisted review lineage"
            )
        return True

    def _evaluate_route(
        self,
        project_id: CanonicalId,
        route: IssueOwnerRoute,
        owner: TaskRecord,
    ) -> MaxReworkGateDecision:
        records = self._tasks.list_tasks(project_id)
        if len(records) > MAX_REWORK_TASK_SCAN:
            raise ReworkTaskGenerationConflictError(
                "max-rework gate scan exceeds MAX_REWORK_TASK_SCAN"
            )

        lineage = [
            record
            for record in records
            if self._is_r9_5_lineage_task(record, route, owner)
        ]
        if len(lineage) > MAX_REWORK_ATTEMPTS:
            raise ReworkTaskGenerationConflictError(
                "persisted review lineage already exceeds frozen rework budget"
            )

        # R9.5 guarantees one persisted task per canonical issue ID. Preserve
        # that invariant here so corrupted lineage data never weakens the gate.
        issue_ids = []
        for record in lineage:
            match = _REWORK_TITLE_RE.fullmatch(record.title)
            assert match is not None
            issue_ids.append(match.group(1))
        if len(issue_ids) != len(set(issue_ids)):
            raise ReworkTaskGenerationConflictError(
                "duplicate canonical issue IDs exist in review rework lineage"
            )

        count = len(lineage)
        allowed = count < MAX_REWORK_ATTEMPTS
        return MaxReworkGateDecision(
            project_id=project_id,
            review_task_id=route.review_task_id,
            target_artifact=route.target_artifact,
            rework_attempts=count,
            max_rework_attempts=MAX_REWORK_ATTEMPTS,
            allowed=allowed,
            target_status=None if allowed else TaskState.NEEDS_HUMAN,
        )

    def evaluate(
        self,
        project_id: CanonicalId | str,
        review_task_id: CanonicalId | str,
        issue: ReviewIssue,
    ) -> MaxReworkGateDecision:
        if not isinstance(issue, ReviewIssue):
            raise TypeError("issue must be ReviewIssue")
        project = _project_id(project_id)
        try:
            route = self._router.route(project, review_task_id, issue)
            owner = self._tasks.open_task(project, route.owner_task_id)
        except (IssueOwnerRoutingError, TaskNotFoundError, TaskStoreConflictError) as exc:
            raise ReworkTaskGenerationConflictError(
                f"max-rework lineage could not be opened safely: {exc}"
            ) from exc
        if owner.role != route.owner_role or not owner.review_required:
            raise ReworkTaskGenerationConflictError(
                "max-rework routed owner does not match review-required producer"
            )
        return self._evaluate_route(project, route, owner)


class DuplicateIssueSuppressor:
    """Suppress repeated root-cause fingerprints inside one exact review lineage.

    R9.7 is read-only by itself. It derives the lineage from the same persisted
    R9.3/R9.4 provenance used by R9.5/R9.6 and matches only the SHA-256 digest
    already stored in bounded R9.5 task titles. A fingerprint in another review
    lineage is independent. Existing legacy duplicates are tolerated; the lowest
    canonical TASK ID is reused and no additional duplicate is persisted.
    """

    def __init__(self, project_store: ProjectStore) -> None:
        if not isinstance(project_store, ProjectStore):
            raise TypeError("project_store must be ProjectStore")
        self._tasks = TaskStore(project_store)
        self._router = IssueOwnerRouter(project_store)

    def _evaluate_route(
        self,
        project_id: CanonicalId,
        route: IssueOwnerRoute,
        issue: ReviewIssue,
        owner: TaskRecord,
    ) -> DuplicateIssueSuppressionDecision:
        digest = _fingerprint_digest(issue)
        records = self._tasks.list_tasks(project_id)
        if len(records) > MAX_REWORK_TASK_SCAN:
            raise ReworkTaskGenerationConflictError(
                "duplicate suppression scan exceeds MAX_REWORK_TASK_SCAN"
            )

        matches: list[tuple[CanonicalId, TaskRecord]] = []
        for record in records:
            if not MaxReworkGate._is_r9_5_lineage_task(record, route, owner):
                continue
            match = _REWORK_TITLE_RE.fullmatch(record.title)
            assert match is not None
            if match.group(2) != digest:
                continue
            try:
                existing_issue_id = CanonicalId.parse(match.group(1))
            except CanonicalIdError as exc:
                raise ReworkTaskGenerationConflictError(
                    "duplicate suppression found non-canonical issue id"
                ) from exc
            if existing_issue_id.kind is not IdKind.ISSUE:
                raise ReworkTaskGenerationConflictError(
                    "duplicate suppression issue id has wrong canonical kind"
                )
            matches.append((existing_issue_id, record))

        if not matches:
            return DuplicateIssueSuppressionDecision(
                project_id=project_id,
                review_task_id=route.review_task_id,
                target_artifact=route.target_artifact,
                fingerprint_digest=digest,
                duplicate=False,
                existing_issue_id=None,
                existing_task_id=None,
            )

        # TaskStore.list_tasks() is canonical TASK-ID ordered. Reuse the first
        # match so an upgrade over pre-R9.7 duplicate data remains deterministic
        # without deleting historical records.
        existing_issue_id, existing_task = matches[0]
        return DuplicateIssueSuppressionDecision(
            project_id=project_id,
            review_task_id=route.review_task_id,
            target_artifact=route.target_artifact,
            fingerprint_digest=digest,
            duplicate=True,
            existing_issue_id=existing_issue_id,
            existing_task_id=existing_task.task_id,
        )

    def evaluate(
        self,
        project_id: CanonicalId | str,
        review_task_id: CanonicalId | str,
        issue: ReviewIssue,
    ) -> DuplicateIssueSuppressionDecision:
        if not isinstance(issue, ReviewIssue):
            raise TypeError("issue must be ReviewIssue")
        project = _project_id(project_id)
        try:
            route = self._router.route(project, review_task_id, issue)
            owner = self._tasks.open_task(project, route.owner_task_id)
        except (IssueOwnerRoutingError, TaskNotFoundError, TaskStoreConflictError) as exc:
            raise ReworkTaskGenerationConflictError(
                f"duplicate suppression lineage could not be opened safely: {exc}"
            ) from exc
        if owner.role != route.owner_role or not owner.review_required:
            raise ReworkTaskGenerationConflictError(
                "duplicate suppression routed owner does not match review-required producer"
            )
        return self._evaluate_route(project, route, issue, owner)


class ReworkTaskGenerator:
    """Persist one targeted owner task for one R9.4-routed review issue.

    R9.5 creates a new TaskRecord rather than mutating/reopening the producer task.
    The new task depends on the reviewer task, consumes the exact reviewed artifact
    version, and inherits the owner's execution contract (role, output schema,
    validators, review requirement, priority and bounded task-attempt budget).

    The review issue itself is not stored as a separate record. A bounded task title
    binds its canonical ISSUE ID to SHA-256(fingerprint) so repeated generation is
    idempotent across restart without persisting semantic review text. After R9.6,
    creation is guarded by the frozen two-attempt review budget; cross-issue
    R9.7 duplicate-fingerprint suppression runs before the R9.6 budget gate so
    repeated root-cause findings reuse a persisted task without consuming a new
    review rework attempt.
    """

    def __init__(self, project_store: ProjectStore) -> None:
        if not isinstance(project_store, ProjectStore):
            raise TypeError("project_store must be ProjectStore")
        self._projects = project_store
        self._tasks = TaskStore(project_store)
        self._router = IssueOwnerRouter(project_store)
        self._duplicate_suppressor = DuplicateIssueSuppressor(project_store)
        self._max_rework_gate = MaxReworkGate(project_store)

    @contextmanager
    def _lock(self, project_id: CanonicalId) -> Iterator[None]:
        try:
            handle = self._projects.open_project(project_id)
        except (ProjectNotFoundError, ProjectStoreConflictError) as exc:
            raise ReworkTaskGenerationConflictError(
                f"project could not be opened: {exc}"
            ) from exc
        lock_path = handle.layout.data_directories["tasks"] / REWORK_TASK_LOCK_FILENAME
        if lock_path.is_symlink():
            raise ReworkTaskGenerationConflictError("rework task lock must not be a symlink")
        if lock_path.exists() and not lock_path.is_file():
            raise ReworkTaskGenerationConflictError("rework task lock must be a regular file")
        try:
            fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
        except OSError as exc:
            raise ReworkTaskGenerationConflictError("rework task lock could not be opened") from exc
        try:
            os.chmod(lock_path, 0o600)
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        except OSError as exc:
            raise ReworkTaskGenerationConflictError("rework task lock failed") from exc
        finally:
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            finally:
                os.close(fd)

    @staticmethod
    def _title(route: IssueOwnerRoute, issue: ReviewIssue) -> str:
        target = route.target_artifact
        return (
            f"{REWORK_TASK_TITLE_PREFIX} {issue.issue_id.value} "
            f"{_fingerprint_digest(issue)} for "
            f"{target.entity_id.value}@v{target.version}"
        )

    @staticmethod
    def _issue_title_prefix(issue: ReviewIssue) -> str:
        return f"{REWORK_TASK_TITLE_PREFIX} {issue.issue_id.value} "

    def _find_existing(
        self,
        project_id: CanonicalId,
        route: IssueOwnerRoute,
        issue: ReviewIssue,
        owner: TaskRecord,
    ) -> TaskRecord | None:
        records = self._tasks.list_tasks(project_id)
        if len(records) > MAX_REWORK_TASK_SCAN:
            raise ReworkTaskGenerationConflictError(
                "rework task recovery exceeds MAX_REWORK_TASK_SCAN"
            )

        expected_title = self._title(route, issue)
        issue_prefix = self._issue_title_prefix(issue)
        matching: list[TaskRecord] = []
        ambiguous: list[TaskRecord] = []

        for record in records:
            if not record.title.startswith(issue_prefix):
                continue
            exact = (
                record.title == expected_title
                and record.role == route.owner_role
                and record.priority == owner.priority
                and record.dependencies == (route.review_task_id,)
                and record.input_artifacts == (route.target_artifact,)
                and record.output_schema == owner.output_schema
                and record.validators == owner.validators
                and record.review_required is owner.review_required
                and record.max_attempts == owner.max_attempts
            )
            (matching if exact else ambiguous).append(record)

        if ambiguous:
            raise ReworkTaskGenerationConflictError(
                "conflicting rework task already uses canonical issue_id"
            )
        if len(matching) > 1:
            raise ReworkTaskGenerationConflictError(
                "multiple R9.5 rework tasks exist for one review issue"
            )
        return matching[0] if matching else None

    def generate(
        self,
        project_id: CanonicalId | str,
        review_task_id: CanonicalId | str,
        issue: ReviewIssue,
    ) -> ReworkTaskGenerationResult:
        if not isinstance(issue, ReviewIssue):
            raise TypeError("issue must be ReviewIssue")
        project = _project_id(project_id)
        try:
            route = self._router.route(project, review_task_id, issue)
        except IssueOwnerRoutingError as exc:
            raise ReworkTaskGenerationConflictError(
                f"issue owner could not be routed safely: {exc}"
            ) from exc

        try:
            owner = self._tasks.open_task(project, route.owner_task_id)
        except (TaskNotFoundError, TaskStoreConflictError) as exc:
            raise ReworkTaskGenerationConflictError(
                f"routed owner task cannot be opened safely: {exc}"
            ) from exc
        if owner.role != route.owner_role:
            raise ReworkTaskGenerationConflictError(
                "routed owner role does not match persisted owner task"
            )
        if not owner.review_required:
            raise ReworkTaskGenerationConflictError(
                "routed owner task is not review-required"
            )

        with self._lock(project):
            existing = self._find_existing(project, route, issue, owner)
            if existing is not None:
                return ReworkTaskGenerationResult(
                    route=route,
                    task=existing,
                    reused_existing=True,
                    suppressed_duplicate=False,
                )

            suppression = self._duplicate_suppressor._evaluate_route(
                project, route, issue, owner
            )
            if suppression.duplicate:
                assert suppression.existing_task_id is not None
                try:
                    duplicate_task = self._tasks.open_task(
                        project, suppression.existing_task_id
                    )
                except (TaskNotFoundError, TaskStoreConflictError) as exc:
                    raise ReworkTaskGenerationConflictError(
                        "suppressed duplicate task cannot be reopened safely"
                    ) from exc
                return ReworkTaskGenerationResult(
                    route=route,
                    task=duplicate_task,
                    reused_existing=True,
                    suppressed_duplicate=True,
                )

            decision = self._max_rework_gate._evaluate_route(project, route, owner)
            if not decision.allowed:
                raise MaxReworkGateExceeded(decision)

            try:
                task = self._tasks.create_task(
                    project,
                    TaskCreateInput(
                        title=self._title(route, issue),
                        role=route.owner_role,
                        priority=owner.priority,
                        dependencies=(route.review_task_id,),
                        input_artifacts=(route.target_artifact,),
                        output_schema=owner.output_schema,
                        validators=owner.validators,
                        review_required=owner.review_required,
                        max_attempts=owner.max_attempts,
                    ),
                )
            except TaskStoreConflictError as exc:
                raise ReworkTaskGenerationConflictError(
                    f"rework task could not be persisted safely: {exc}"
                ) from exc

            if task.status is not TaskState.CREATED or task.attempt != 0:
                raise ReworkTaskGenerationConflictError(
                    "persisted rework task did not start in CREATED/attempt=0"
                )
            return ReworkTaskGenerationResult(
                route=route,
                task=task,
                reused_existing=False,
                suppressed_duplicate=False,
            )
