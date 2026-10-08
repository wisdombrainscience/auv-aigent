from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import fcntl
import hashlib
import json
import os
from typing import Any, Iterator

from ai.agents.output import AGENT_RESULT_OUTPUT_SCHEMA_ID
from ai.agents.roles import DEFAULT_ROLE_REGISTRY, RoleRegistry, RoleRegistryError
from ai.artifacts.schema import ArtifactInputVersion
from ai.contracts.ids import CanonicalId, IdKind
from ai.contracts.provenance import ProvenanceClass
from ai.contracts.task_states import TaskState
from ai.orchestrator.validator import MAX_TASKS_PER_PLAN
from ai.project.requirement_versions import (
    RequirementVersionConflictError,
    RequirementVersionNotFoundError,
    RequirementVersionStore,
)
from ai.project.requirements import RequirementSetStatus
from ai.project.store import ProjectNotFoundError, ProjectStore, ProjectStoreConflictError
from ai.simulation_feedback.invalidation import SimulationLineageInvalidationResult
from ai.simulation_feedback.routing import SimulationIssueRolePlan
from ai.simulation_feedback.taxonomy import MAX_SIMULATION_ISSUES
from ai.tasks.schema import MAX_TASK_ATTEMPTS, MAX_TASK_INPUT_ARTIFACTS, TaskRecord
from ai.tasks.store import TaskCreateInput, TaskStore, TaskStoreConflictError


SIMULATION_REWORK_TASK_GENERATION_CONTRACT_VERSION = "1.0"
SIMULATION_REWORK_TASK_TITLE_PREFIX = "Simulation rework"
SIMULATION_REWORK_TASK_OUTPUT_SCHEMA = AGENT_RESULT_OUTPUT_SCHEMA_ID
SIMULATION_REWORK_TASK_PRIORITY = 0
SIMULATION_REWORK_TASK_LOCK_FILENAME = ".simulation-rework-task-generation.lock"
MAX_SIMULATION_REWORK_TASK_SCAN = 4096
MAX_SIMULATION_REWORK_TASKS = min(MAX_SIMULATION_ISSUES, MAX_TASKS_PER_PLAN)


class SimulationReworkTaskGenerationError(RuntimeError):
    """Base error for deterministic F12.5 simulation rework task generation."""


class SimulationReworkTaskGenerationConflictError(SimulationReworkTaskGenerationError):
    """Raised when simulation feedback cannot be materialized safely as tasks."""


@dataclass(frozen=True)
class SimulationReworkTaskBinding:
    """One persisted F12.5 task for one routed engineering owner."""

    owner_role: str
    issue_fingerprints: tuple[str, ...]
    task: TaskRecord
    reused_existing: bool

    def __post_init__(self) -> None:
        if not isinstance(self.owner_role, str) or not self.owner_role:
            raise SimulationReworkTaskGenerationConflictError(
                "owner_role must be non-empty text"
            )
        if not isinstance(self.issue_fingerprints, tuple) or not self.issue_fingerprints:
            raise SimulationReworkTaskGenerationConflictError(
                "issue_fingerprints must be a non-empty tuple"
            )
        if len(set(self.issue_fingerprints)) != len(self.issue_fingerprints):
            raise SimulationReworkTaskGenerationConflictError(
                "issue_fingerprints contains duplicates"
            )
        if not isinstance(self.task, TaskRecord):
            raise SimulationReworkTaskGenerationConflictError("task must be TaskRecord")
        if self.task.role != self.owner_role:
            raise SimulationReworkTaskGenerationConflictError(
                "task role does not match routed engineering owner"
            )
        if not isinstance(self.reused_existing, bool):
            raise SimulationReworkTaskGenerationConflictError(
                "reused_existing must be boolean"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "owner_role": self.owner_role,
            "issue_fingerprints": list(self.issue_fingerprints),
            "task": self.task.to_dict(),
            "reused_existing": self.reused_existing,
        }


@dataclass(frozen=True)
class SimulationReworkTaskGenerationResult:
    """Bounded F12.5 generation result preserving verifier/project lineage."""

    project_id: CanonicalId
    requirement_version: int
    source_artifacts: tuple[ArtifactInputVersion, ...]
    runtime_run_id: str
    mission_id: str
    scenario_id: str
    verification_sha256: str
    bindings: tuple[SimulationReworkTaskBinding, ...]
    provenance: ProvenanceClass = ProvenanceClass.VERIFIER_RESULT

    def __post_init__(self) -> None:
        if not isinstance(self.project_id, CanonicalId) or self.project_id.kind is not IdKind.PROJECT:
            raise SimulationReworkTaskGenerationConflictError(
                "project_id must identify a project"
            )
        if isinstance(self.requirement_version, bool) or not isinstance(self.requirement_version, int) or self.requirement_version <= 0:
            raise SimulationReworkTaskGenerationConflictError(
                "requirement_version must be a positive integer"
            )
        if not isinstance(self.source_artifacts, tuple) or any(
            not isinstance(item, ArtifactInputVersion) for item in self.source_artifacts
        ):
            raise SimulationReworkTaskGenerationConflictError(
                "source_artifacts must preserve exact artifact lineage"
            )
        for field in ("runtime_run_id", "mission_id", "scenario_id"):
            value = getattr(self, field)
            if not isinstance(value, str) or not value or value != value.strip():
                raise SimulationReworkTaskGenerationConflictError(
                    f"{field} must be clean non-empty text"
                )
        if (
            not isinstance(self.verification_sha256, str)
            or len(self.verification_sha256) != 64
            or any(ch not in "0123456789abcdef" for ch in self.verification_sha256)
        ):
            raise SimulationReworkTaskGenerationConflictError(
                "verification_sha256 must be lowercase sha256"
            )
        if not isinstance(self.bindings, tuple) or any(
            not isinstance(item, SimulationReworkTaskBinding) for item in self.bindings
        ):
            raise SimulationReworkTaskGenerationConflictError(
                "bindings must contain SimulationReworkTaskBinding values"
            )
        if len(self.bindings) > MAX_SIMULATION_REWORK_TASKS:
            raise SimulationReworkTaskGenerationConflictError(
                "bindings exceeds bounded simulation rework tasks"
            )
        owners = tuple(item.owner_role for item in self.bindings)
        if len(set(owners)) != len(owners):
            raise SimulationReworkTaskGenerationConflictError(
                "bindings contains duplicate engineering owner"
            )
        if self.provenance is not ProvenanceClass.VERIFIER_RESULT:
            raise SimulationReworkTaskGenerationConflictError(
                "provenance must remain VERIFIER_RESULT"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "contract_version": SIMULATION_REWORK_TASK_GENERATION_CONTRACT_VERSION,
            "project_id": self.project_id.value,
            "requirement_version": self.requirement_version,
            "source_artifacts": [item.to_dict() for item in self.source_artifacts],
            "runtime_run_id": self.runtime_run_id,
            "mission_id": self.mission_id,
            "scenario_id": self.scenario_id,
            "verification_sha256": self.verification_sha256,
            "bindings": [item.to_dict() for item in self.bindings],
            "provenance": self.provenance.value,
        }


def _feedback_consistency(
    plan: SimulationIssueRolePlan,
    invalidation: SimulationLineageInvalidationResult,
) -> None:
    fields = (
        "project_id",
        "requirement_version",
        "source_artifacts",
        "runtime_run_id",
        "mission_id",
        "scenario_id",
        "verification_sha256",
        "provenance",
    )
    for field in fields:
        if getattr(plan, field) != getattr(invalidation, field):
            raise SimulationReworkTaskGenerationConflictError(
                f"F12.3/F12.4 lineage mismatch: {field}"
            )

    fingerprints = tuple(route.issue.fingerprint for route in plan.routes)
    if fingerprints != invalidation.issue_fingerprints:
        raise SimulationReworkTaskGenerationConflictError(
            "F12.3/F12.4 issue fingerprint lineage mismatch"
        )

    if plan.routes:
        expected_roots = tuple(
            sorted(
                plan.source_artifacts,
                key=lambda item: (item.entity_id.value, item.version),
            )
        )
        if invalidation.invalidation_roots != expected_roots:
            raise SimulationReworkTaskGenerationConflictError(
                "F12.4 invalidation roots do not match simulation source lineage"
            )
    elif invalidation.invalidation_roots:
        raise SimulationReworkTaskGenerationConflictError(
            "PASS feedback must not contain invalidation roots"
        )


def _requirement_context(
    projects: ProjectStore,
    plan: SimulationIssueRolePlan,
) -> tuple[ArtifactInputVersion, ...]:
    try:
        version = RequirementVersionStore(projects).open_version(
            plan.project_id,
            plan.requirement_version,
        )
    except (RequirementVersionNotFoundError, RequirementVersionConflictError) as exc:
        raise SimulationReworkTaskGenerationConflictError(
            "simulation requirement version cannot be opened safely"
        ) from exc
    if version.requirement_set.status is not RequirementSetStatus.APPROVED:
        raise SimulationReworkTaskGenerationConflictError(
            "simulation requirement version must remain APPROVED"
        )
    requirements = tuple(
        sorted(
            (
                ArtifactInputVersion(
                    entity_id=item.requirement_id,
                    version=version.version,
                )
                for item in version.requirement_set.requirements
            ),
            key=lambda item: item.entity_id.value,
        )
    )
    if len(requirements) > MAX_TASK_INPUT_ARTIFACTS:
        raise SimulationReworkTaskGenerationConflictError(
            "approved requirement context exceeds MAX_TASK_INPUT_ARTIFACTS"
        )
    return requirements


def _owner_groups(plan: SimulationIssueRolePlan) -> tuple[tuple[str, tuple[Any, ...]], ...]:
    grouped: dict[str, list[Any]] = {}
    for route in plan.routes:
        grouped.setdefault(route.owner_role, []).append(route)
    if len(grouped) > MAX_SIMULATION_REWORK_TASKS:
        raise SimulationReworkTaskGenerationConflictError(
            "distinct routed owners exceed bounded simulation rework tasks"
        )
    return tuple((owner, tuple(grouped[owner])) for owner in sorted(grouped))


def _feedback_digest(
    plan: SimulationIssueRolePlan,
    *,
    owner_role: str,
    issue_fingerprints: tuple[str, ...],
) -> str:
    payload = {
        "project_id": plan.project_id.value,
        "requirement_version": plan.requirement_version,
        "source_artifacts": [item.to_dict() for item in plan.source_artifacts],
        "runtime_run_id": plan.runtime_run_id,
        "mission_id": plan.mission_id,
        "scenario_id": plan.scenario_id,
        "verification_sha256": plan.verification_sha256,
        "owner_role": owner_role,
        "issue_fingerprints": list(issue_fingerprints),
    }
    raw = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _title(
    plan: SimulationIssueRolePlan,
    *,
    owner_role: str,
    routes: tuple[Any, ...],
) -> str:
    fingerprints = tuple(route.issue.fingerprint for route in routes)
    digest = _feedback_digest(
        plan,
        owner_role=owner_role,
        issue_fingerprints=fingerprints,
    )
    checks = ",".join(route.issue.check_id for route in routes)
    return f"{SIMULATION_REWORK_TASK_TITLE_PREFIX} {owner_role} {digest} checks={checks}"


class SimulationReworkTaskGenerator:
    """Persist bounded owner-targeted TaskRecords for one F12.4 feedback event.

    F12.5 generates tasks only. It does not execute them, create ISSUE records,
    authorize a simulation retry, change project/task state to NEEDS_HUMAN, call
    a provider, or invoke the UAV runtime.
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
        self._projects = project_store
        self._tasks = TaskStore(project_store)
        self._roles = role_registry

    @contextmanager
    def _lock(self, project_id: CanonicalId) -> Iterator[None]:
        try:
            handle = self._projects.open_project(project_id)
        except (ProjectNotFoundError, ProjectStoreConflictError) as exc:
            raise SimulationReworkTaskGenerationConflictError(
                "project could not be opened safely"
            ) from exc
        path = handle.layout.data_directories["tasks"] / SIMULATION_REWORK_TASK_LOCK_FILENAME
        if path.is_symlink():
            raise SimulationReworkTaskGenerationConflictError(
                "simulation rework lock must not be a symlink"
            )
        if path.exists() and not path.is_file():
            raise SimulationReworkTaskGenerationConflictError(
                "simulation rework lock must be a regular file"
            )
        try:
            fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
        except OSError as exc:
            raise SimulationReworkTaskGenerationConflictError(
                "simulation rework lock could not be opened"
            ) from exc
        try:
            os.chmod(path, 0o600)
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        except OSError as exc:
            raise SimulationReworkTaskGenerationConflictError(
                "simulation rework lock failed"
            ) from exc
        finally:
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            finally:
                os.close(fd)

    def _existing(
        self,
        plan: SimulationIssueRolePlan,
        *,
        owner_role: str,
        routes: tuple[Any, ...],
        requirements: tuple[ArtifactInputVersion, ...],
    ) -> TaskRecord | None:
        records = self._tasks.list_tasks(plan.project_id)
        if len(records) > MAX_SIMULATION_REWORK_TASK_SCAN:
            raise SimulationReworkTaskGenerationConflictError(
                "simulation rework recovery exceeds MAX_SIMULATION_REWORK_TASK_SCAN"
            )
        expected_title = _title(plan, owner_role=owner_role, routes=routes)
        matching: list[TaskRecord] = []
        conflicts: list[TaskRecord] = []
        for record in records:
            if record.title != expected_title:
                continue
            exact = (
                record.role == owner_role
                and record.priority == SIMULATION_REWORK_TASK_PRIORITY
                and record.dependencies == ()
                and record.input_artifacts == requirements
                and record.output_schema == SIMULATION_REWORK_TASK_OUTPUT_SCHEMA
                and record.validators == ()
                and record.review_required is True
                and record.max_attempts == MAX_TASK_ATTEMPTS
            )
            (matching if exact else conflicts).append(record)
        if conflicts:
            raise SimulationReworkTaskGenerationConflictError(
                "conflicting task already uses deterministic simulation rework identity"
            )
        if len(matching) > 1:
            raise SimulationReworkTaskGenerationConflictError(
                "multiple simulation rework tasks exist for one owner feedback event"
            )
        return matching[0] if matching else None

    def generate(
        self,
        plan: SimulationIssueRolePlan,
        invalidation: SimulationLineageInvalidationResult,
    ) -> SimulationReworkTaskGenerationResult:
        if not isinstance(plan, SimulationIssueRolePlan):
            raise TypeError("plan must be SimulationIssueRolePlan")
        if not isinstance(invalidation, SimulationLineageInvalidationResult):
            raise TypeError("invalidation must be SimulationLineageInvalidationResult")
        _feedback_consistency(plan, invalidation)

        if not plan.routes:
            return SimulationReworkTaskGenerationResult(
                project_id=plan.project_id,
                requirement_version=plan.requirement_version,
                source_artifacts=plan.source_artifacts,
                runtime_run_id=plan.runtime_run_id,
                mission_id=plan.mission_id,
                scenario_id=plan.scenario_id,
                verification_sha256=plan.verification_sha256,
                bindings=(),
            )

        requirements = _requirement_context(self._projects, plan)
        groups = _owner_groups(plan)

        # Validate every owner before any task persistence.
        for owner_role, _routes in groups:
            try:
                self._roles.require_known(owner_role)
            except RoleRegistryError as exc:
                raise SimulationReworkTaskGenerationConflictError(
                    f"routed engineering owner is not active: {owner_role}"
                ) from exc

        bindings: list[SimulationReworkTaskBinding] = []
        with self._lock(plan.project_id):
            existing_by_owner = {
                owner_role: self._existing(
                    plan,
                    owner_role=owner_role,
                    routes=routes,
                    requirements=requirements,
                )
                for owner_role, routes in groups
            }
            for owner_role, routes in groups:
                fingerprints = tuple(route.issue.fingerprint for route in routes)
                existing = existing_by_owner[owner_role]
                if existing is not None:
                    bindings.append(
                        SimulationReworkTaskBinding(
                            owner_role=owner_role,
                            issue_fingerprints=fingerprints,
                            task=existing,
                            reused_existing=True,
                        )
                    )
                    continue
                try:
                    task = self._tasks.create_task(
                        plan.project_id,
                        TaskCreateInput(
                            title=_title(plan, owner_role=owner_role, routes=routes),
                            role=owner_role,
                            priority=SIMULATION_REWORK_TASK_PRIORITY,
                            dependencies=(),
                            input_artifacts=requirements,
                            output_schema=SIMULATION_REWORK_TASK_OUTPUT_SCHEMA,
                            validators=(),
                            review_required=True,
                            max_attempts=MAX_TASK_ATTEMPTS,
                        ),
                    )
                except TaskStoreConflictError as exc:
                    raise SimulationReworkTaskGenerationConflictError(
                        "simulation rework task could not be persisted safely"
                    ) from exc
                if task.status is not TaskState.CREATED or task.attempt != 0:
                    raise SimulationReworkTaskGenerationConflictError(
                        "persisted simulation rework task did not start CREATED/attempt=0"
                    )
                bindings.append(
                    SimulationReworkTaskBinding(
                        owner_role=owner_role,
                        issue_fingerprints=fingerprints,
                        task=task,
                        reused_existing=False,
                    )
                )

        return SimulationReworkTaskGenerationResult(
            project_id=plan.project_id,
            requirement_version=plan.requirement_version,
            source_artifacts=plan.source_artifacts,
            runtime_run_id=plan.runtime_run_id,
            mission_id=plan.mission_id,
            scenario_id=plan.scenario_id,
            verification_sha256=plan.verification_sha256,
            bindings=tuple(bindings),
        )
