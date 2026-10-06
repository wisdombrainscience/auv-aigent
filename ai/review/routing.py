from __future__ import annotations

from dataclasses import dataclass

from ai.agents.roles import DEFAULT_ROLE_REGISTRY, RoleRegistry, RoleRegistryError
from ai.artifacts.schema import ArtifactInputVersion, ArtifactRecord
from ai.artifacts.versions import (
    ArtifactVersionConflictError,
    ArtifactVersionNotFoundError,
    ArtifactVersionStore,
)
from ai.contracts.ids import CanonicalId, CanonicalIdError, IdKind
from ai.contracts.review_result import ReviewIssue
from ai.project.store import ProjectStore
from ai.review.role import REVIEWER_ROLE_ID
from ai.review.tasks import REVIEW_TASK_OUTPUT_SCHEMA, REVIEW_TASK_TITLE_PREFIX
from ai.tasks.schema import MAX_TASK_ATTEMPTS, TaskRecord
from ai.tasks.store import TaskNotFoundError, TaskStore, TaskStoreConflictError


ISSUE_OWNER_ROUTING_CONTRACT_VERSION = "1.0"


class IssueOwnerRoutingError(RuntimeError):
    """Base error for deterministic R9.4 issue-owner routing."""


class IssueOwnerRoutingConflictError(IssueOwnerRoutingError):
    """Raised when persisted review provenance cannot identify one safe owner."""


@dataclass(frozen=True)
class IssueOwnerRoute:
    """Immutable advisory route from one review issue to its artifact producer.

    R9.4 does not persist this object or create a rework task. The owner is
    derived only from persisted review-task/artifact/task provenance; semantic
    issue fields such as severity/category/field never choose the owner.
    """

    project_id: CanonicalId
    issue_id: CanonicalId
    fingerprint: str
    review_task_id: CanonicalId
    target_artifact: ArtifactInputVersion
    owner_task_id: CanonicalId
    owner_role: str

    def __post_init__(self) -> None:
        if not isinstance(self.project_id, CanonicalId) or self.project_id.kind is not IdKind.PROJECT:
            raise IssueOwnerRoutingConflictError("project_id must identify a project")
        if not isinstance(self.issue_id, CanonicalId) or self.issue_id.kind is not IdKind.ISSUE:
            raise IssueOwnerRoutingConflictError("issue_id must identify an issue")
        if not isinstance(self.fingerprint, str) or not self.fingerprint or self.fingerprint != self.fingerprint.strip():
            raise IssueOwnerRoutingConflictError("fingerprint must be clean non-empty text")
        if not isinstance(self.review_task_id, CanonicalId) or self.review_task_id.kind is not IdKind.TASK:
            raise IssueOwnerRoutingConflictError("review_task_id must identify a task")
        if not isinstance(self.target_artifact, ArtifactInputVersion):
            raise IssueOwnerRoutingConflictError("target_artifact must be ArtifactInputVersion")
        if self.target_artifact.entity_id.kind is not IdKind.ARTIFACT:
            raise IssueOwnerRoutingConflictError("target_artifact must identify an artifact version")
        if not isinstance(self.owner_task_id, CanonicalId) or self.owner_task_id.kind is not IdKind.TASK:
            raise IssueOwnerRoutingConflictError("owner_task_id must identify a task")
        if not isinstance(self.owner_role, str) or not self.owner_role or self.owner_role != self.owner_role.strip():
            raise IssueOwnerRoutingConflictError("owner_role must be clean non-empty text")


def _project_id(raw: CanonicalId | str) -> CanonicalId:
    try:
        value = raw if isinstance(raw, CanonicalId) else CanonicalId.parse(raw)
    except (CanonicalIdError, TypeError) as exc:
        raise IssueOwnerRoutingConflictError("project_id must be canonical") from exc
    if value.kind is not IdKind.PROJECT:
        raise IssueOwnerRoutingConflictError("project_id must identify a project")
    return value


class IssueOwnerRouter:
    """Resolve one ReviewIssue to the producer that owns the reviewed artifact.

    Source-of-truth chain:

        review TaskRecord
             -> exact input artifact version
             -> ArtifactRecord.producer_task
             -> producer TaskRecord.role

    The review task must match the R9.3 generated-task shape. No issue field is
    interpreted as routing policy, and this class has no write/rework authority.
    """

    def __init__(
        self,
        project_store: ProjectStore,
        role_registry: RoleRegistry = DEFAULT_ROLE_REGISTRY,
    ) -> None:
        if not isinstance(project_store, ProjectStore):
            raise TypeError("project_store must be ProjectStore")
        if not isinstance(role_registry, RoleRegistry):
            raise TypeError("role_registry must be RoleRegistry")
        self._tasks = TaskStore(project_store)
        self._artifacts = ArtifactVersionStore(project_store)
        self._roles = role_registry

    @staticmethod
    def _require_review_task_shape(review_task: TaskRecord) -> ArtifactInputVersion:
        if review_task.role != REVIEWER_ROLE_ID:
            raise IssueOwnerRoutingConflictError("routing source task must use reviewer role")
        if review_task.output_schema != REVIEW_TASK_OUTPUT_SCHEMA:
            raise IssueOwnerRoutingConflictError("routing source task must use R9.3 review output schema")
        if review_task.validators != ():
            raise IssueOwnerRoutingConflictError("routing source task validators do not match R9.3")
        if review_task.review_required is not False:
            raise IssueOwnerRoutingConflictError("routing source task must not request recursive review")
        if review_task.max_attempts != MAX_TASK_ATTEMPTS:
            raise IssueOwnerRoutingConflictError("routing source task retry budget does not match R9.3")
        if len(review_task.input_artifacts) != 1:
            raise IssueOwnerRoutingConflictError("routing source task must reference exactly one artifact version")
        if len(review_task.dependencies) != 1:
            raise IssueOwnerRoutingConflictError("routing source task must depend on exactly one producer task")
        target = review_task.input_artifacts[0]
        if target.entity_id.kind is not IdKind.ARTIFACT:
            raise IssueOwnerRoutingConflictError("routing source input must identify an artifact version")
        expected_title = f"{REVIEW_TASK_TITLE_PREFIX} {target.entity_id.value}@v{target.version}"
        if review_task.title != expected_title:
            raise IssueOwnerRoutingConflictError("routing source task title does not match R9.3 target")
        return target

    def route(
        self,
        project_id: CanonicalId | str,
        review_task_id: CanonicalId | str,
        issue: ReviewIssue,
    ) -> IssueOwnerRoute:
        if not isinstance(issue, ReviewIssue):
            raise TypeError("issue must be ReviewIssue")
        project = _project_id(project_id)
        try:
            review_task = self._tasks.open_task(project, review_task_id)
        except (TaskNotFoundError, TaskStoreConflictError) as exc:
            raise IssueOwnerRoutingConflictError(
                f"review task cannot be opened safely: {exc}"
            ) from exc

        target = self._require_review_task_shape(review_task)
        try:
            artifact = self._artifacts.open_version(
                project, target.entity_id, target.version
            )
        except (ArtifactVersionNotFoundError, ArtifactVersionConflictError) as exc:
            raise IssueOwnerRoutingConflictError(
                f"review target artifact cannot be opened safely: {exc}"
            ) from exc

        producer_id = review_task.dependencies[0]
        if artifact.producer_task != producer_id:
            raise IssueOwnerRoutingConflictError(
                "review task dependency does not match artifact producer"
            )
        try:
            producer = self._tasks.open_task(project, producer_id)
        except (TaskNotFoundError, TaskStoreConflictError) as exc:
            raise IssueOwnerRoutingConflictError(
                f"artifact producer task cannot be opened safely: {exc}"
            ) from exc
        if not producer.review_required:
            raise IssueOwnerRoutingConflictError(
                "artifact producer task does not own a review-required output"
            )
        if producer.priority != review_task.priority:
            raise IssueOwnerRoutingConflictError(
                "review task priority does not match artifact producer"
            )
        try:
            owner_role = self._roles.require_known(producer.role)
        except RoleRegistryError as exc:
            raise IssueOwnerRoutingConflictError(
                f"artifact producer role is not an enabled owner: {producer.role}"
            ) from exc

        return IssueOwnerRoute(
            project_id=project,
            issue_id=issue.issue_id,
            fingerprint=issue.fingerprint,
            review_task_id=review_task.task_id,
            target_artifact=target,
            owner_task_id=producer.task_id,
            owner_role=owner_role,
        )
