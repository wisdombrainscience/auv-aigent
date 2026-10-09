from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
import sqlite3
from typing import Any

from ai.contracts.ids import CanonicalId, IdKind
from ai.contracts.provenance import ProvenanceClass
from ai.contracts.task_states import TaskState
from ai.project.store import ProjectNotFoundError, ProjectStore, ProjectStoreConflictError
from ai.simulation_feedback import retry as retry_module
from ai.simulation_feedback.rework import SimulationReworkTaskGenerationResult
from ai.tasks.store import TaskNotFoundError, TaskStore, TaskStoreConflictError


SIMULATION_NEEDS_HUMAN_CONTRACT_VERSION = "1.0"


class SimulationNeedsHumanError(RuntimeError):
    """Base error for deterministic F12.7 simulation human-gate evaluation."""


class SimulationNeedsHumanConflictError(SimulationNeedsHumanError):
    """Raised when persisted feedback/retry lineage cannot be evaluated safely."""


class SimulationHumanGateReason(str, Enum):
    VERIFIER_PASS = "VERIFIER_PASS"
    REWORK_IN_PROGRESS = "REWORK_IN_PROGRESS"
    AUTO_RETRY_AVAILABLE = "AUTO_RETRY_AVAILABLE"
    REWORK_TASK_NEEDS_HUMAN = "REWORK_TASK_NEEDS_HUMAN"
    REWORK_TASK_TERMINAL_FAILURE = "REWORK_TASK_TERMINAL_FAILURE"
    RETRY_BUDGET_EXHAUSTED = "RETRY_BUDGET_EXHAUSTED"
    RETRY_CRASH_RESERVATION = "RETRY_CRASH_RESERVATION"
    RETRY_PRECONDITION_BLOCKED = "RETRY_PRECONDITION_BLOCKED"
    RETRY_REGISTRY_INVALID = "RETRY_REGISTRY_INVALID"
    PARENT_LINK_INVALID = "PARENT_LINK_INVALID"


@dataclass(frozen=True)
class SimulationHumanGateDecision:
    """Read-only F12.7 outcome for one exact simulation feedback lineage.

    The result deliberately mirrors the R9.6 human-gate precedent: it returns a
    typed NEEDS_HUMAN outcome without forcing an unrelated TaskRecord through an
    illegal lifecycle edge (for example DONE -> NEEDS_HUMAN).
    """

    project_id: CanonicalId
    runtime_run_id: str
    root_runtime_run_id: str
    retry_attempts_consumed: int
    max_retry_attempts: int
    reason: SimulationHumanGateReason
    target_status: TaskState | None
    rework_task_ids: tuple[CanonicalId, ...]
    provenance: ProvenanceClass = ProvenanceClass.VERIFIER_RESULT

    def __post_init__(self) -> None:
        if not isinstance(self.project_id, CanonicalId) or self.project_id.kind is not IdKind.PROJECT:
            raise SimulationNeedsHumanConflictError("project_id must identify a project")
        for field in ("runtime_run_id", "root_runtime_run_id"):
            value = getattr(self, field)
            if not isinstance(value, str) or not value or value != value.strip():
                raise SimulationNeedsHumanConflictError(f"{field} must be clean non-empty text")
        if isinstance(self.retry_attempts_consumed, bool) or not isinstance(self.retry_attempts_consumed, int):
            raise SimulationNeedsHumanConflictError("retry_attempts_consumed must be an integer")
        if not 0 <= self.retry_attempts_consumed <= retry_module.MAX_SIMULATION_RETRY_ATTEMPTS:
            raise SimulationNeedsHumanConflictError("retry_attempts_consumed is outside frozen bounds")
        if self.max_retry_attempts != retry_module.MAX_SIMULATION_RETRY_ATTEMPTS:
            raise SimulationNeedsHumanConflictError("max_retry_attempts must match the frozen retry limit")
        if not isinstance(self.reason, SimulationHumanGateReason):
            raise SimulationNeedsHumanConflictError("reason must be SimulationHumanGateReason")
        human_reasons = {
            SimulationHumanGateReason.REWORK_TASK_NEEDS_HUMAN,
            SimulationHumanGateReason.REWORK_TASK_TERMINAL_FAILURE,
            SimulationHumanGateReason.RETRY_BUDGET_EXHAUSTED,
            SimulationHumanGateReason.RETRY_CRASH_RESERVATION,
            SimulationHumanGateReason.RETRY_PRECONDITION_BLOCKED,
            SimulationHumanGateReason.RETRY_REGISTRY_INVALID,
            SimulationHumanGateReason.PARENT_LINK_INVALID,
        }
        expected = TaskState.NEEDS_HUMAN if self.reason in human_reasons else None
        if self.target_status is not expected:
            raise SimulationNeedsHumanConflictError("target_status is inconsistent with human-gate reason")
        if not isinstance(self.rework_task_ids, tuple):
            raise SimulationNeedsHumanConflictError("rework_task_ids must be a tuple")
        if any(not isinstance(item, CanonicalId) or item.kind is not IdKind.TASK for item in self.rework_task_ids):
            raise SimulationNeedsHumanConflictError("rework_task_ids must contain canonical task IDs")
        if len(set(self.rework_task_ids)) != len(self.rework_task_ids):
            raise SimulationNeedsHumanConflictError("rework_task_ids contains duplicates")
        if self.provenance is not ProvenanceClass.VERIFIER_RESULT:
            raise SimulationNeedsHumanConflictError("provenance must remain VERIFIER_RESULT")

    @property
    def needs_human(self) -> bool:
        return self.target_status is TaskState.NEEDS_HUMAN

    def to_dict(self) -> dict[str, Any]:
        return {
            "contract_version": SIMULATION_NEEDS_HUMAN_CONTRACT_VERSION,
            "project_id": self.project_id.value,
            "runtime_run_id": self.runtime_run_id,
            "root_runtime_run_id": self.root_runtime_run_id,
            "retry_attempts_consumed": self.retry_attempts_consumed,
            "max_retry_attempts": self.max_retry_attempts,
            "reason": self.reason.value,
            "target_status": None if self.target_status is None else self.target_status.value,
            "rework_task_ids": [item.value for item in self.rework_task_ids],
            "provenance": self.provenance.value,
        }


def _current_tasks(
    projects: ProjectStore,
    generation: SimulationReworkTaskGenerationResult,
) -> tuple[Any, ...]:
    tasks = TaskStore(projects)
    current_records = []
    for binding in generation.bindings:
        try:
            current = tasks.open_task(generation.project_id, binding.task.task_id)
        except (TaskNotFoundError, TaskStoreConflictError) as exc:
            raise SimulationNeedsHumanConflictError("F12.5 rework task is unavailable or corrupt") from exc
        frozen = binding.task
        for field in (
            "task_id",
            "project_id",
            "title",
            "role",
            "priority",
            "dependencies",
            "input_artifacts",
            "output_schema",
            "validators",
            "review_required",
            "max_attempts",
        ):
            if getattr(current, field) != getattr(frozen, field):
                raise SimulationNeedsHumanConflictError(
                    f"F12.5 rework task contract changed after generation: {field}"
                )
        current_records.append(current)
    return tuple(current_records)


def _registry_path_readonly(projects: ProjectStore, project_id: CanonicalId) -> Path:
    try:
        handle = projects.open_project(project_id)
    except (ProjectNotFoundError, ProjectStoreConflictError) as exc:
        raise SimulationNeedsHumanConflictError("project could not be opened safely") from exc
    simulation_dir = handle.layout.data_directories["simulation"]
    if simulation_dir.is_symlink():
        raise SimulationNeedsHumanConflictError("simulation directory must not be a symbolic link")
    path = simulation_dir / retry_module.SIMULATION_RETRY_REGISTRY_DB_FILENAME
    if path.is_symlink():
        raise SimulationNeedsHumanConflictError("simulation retry registry must not be a symbolic link")
    return path


def _retry_chain_state(
    projects: ProjectStore,
    generation: SimulationReworkTaskGenerationResult,
) -> tuple[str, int, bool, bool]:
    """Return root id, max attempt consumed, reserved-present, registry-invalid."""

    path = _registry_path_readonly(projects, generation.project_id)
    if not path.exists():
        return generation.runtime_run_id, 0, False, False
    if not path.is_file():
        return generation.runtime_run_id, 0, False, True

    try:
        connection = sqlite3.connect(path)
        try:
            retry_module.verify_simulation_retry_registry(connection)
            linked_parent = connection.execute(
                """
                SELECT root_runtime_run_id,retry_attempt
                FROM simulation_retry_attempts
                WHERE runtime_run_id=? AND state='LINKED'
                """,
                (generation.runtime_run_id,),
            ).fetchall()
            if len(linked_parent) > 1:
                return generation.runtime_run_id, 0, False, True
            if linked_parent:
                root_runtime_run_id = linked_parent[0][0]
            else:
                roots = {
                    row[0]
                    for row in connection.execute(
                        """
                        SELECT root_runtime_run_id
                        FROM simulation_retry_attempts
                        WHERE parent_runtime_run_id=?
                        """,
                        (generation.runtime_run_id,),
                    ).fetchall()
                }
                if len(roots) > 1:
                    return generation.runtime_run_id, 0, False, True
                root_runtime_run_id = next(iter(roots), generation.runtime_run_id)

            rows = connection.execute(
                """
                SELECT retry_attempt,state
                FROM simulation_retry_attempts
                WHERE root_runtime_run_id=?
                ORDER BY retry_attempt
                """,
                (root_runtime_run_id,),
            ).fetchall()
            attempts = tuple(int(row[0]) for row in rows)
            if attempts and attempts != tuple(range(1, max(attempts) + 1)):
                return root_runtime_run_id, 0, False, True
            consumed = 0 if not attempts else max(attempts)
            reserved = any(row[1] == "RESERVED" for row in rows)
            return root_runtime_run_id, consumed, reserved, False
        finally:
            connection.close()
    except (sqlite3.Error, retry_module.SimulationRetryConflictError):
        return generation.runtime_run_id, 0, False, True


def _decision(
    generation: SimulationReworkTaskGenerationResult,
    *,
    root_runtime_run_id: str,
    consumed: int,
    reason: SimulationHumanGateReason,
    tasks: tuple[Any, ...],
) -> SimulationHumanGateDecision:
    human = reason in {
        SimulationHumanGateReason.REWORK_TASK_NEEDS_HUMAN,
        SimulationHumanGateReason.REWORK_TASK_TERMINAL_FAILURE,
        SimulationHumanGateReason.RETRY_BUDGET_EXHAUSTED,
        SimulationHumanGateReason.RETRY_CRASH_RESERVATION,
        SimulationHumanGateReason.RETRY_PRECONDITION_BLOCKED,
        SimulationHumanGateReason.RETRY_REGISTRY_INVALID,
        SimulationHumanGateReason.PARENT_LINK_INVALID,
    }
    return SimulationHumanGateDecision(
        project_id=generation.project_id,
        runtime_run_id=generation.runtime_run_id,
        root_runtime_run_id=root_runtime_run_id,
        retry_attempts_consumed=consumed,
        max_retry_attempts=retry_module.MAX_SIMULATION_RETRY_ATTEMPTS,
        reason=reason,
        target_status=TaskState.NEEDS_HUMAN if human else None,
        rework_task_ids=tuple(record.task_id for record in tasks),
    )


def evaluate_simulation_needs_human(
    projects: ProjectStore,
    deployment_root: Path,
    generation: SimulationReworkTaskGenerationResult,
) -> SimulationHumanGateDecision:
    """Evaluate the final bounded simulation human gate without mutation.

    This function never executes a task, invokes the runtime, writes Project
    State/artifact/task/retry registries, or reinterprets verifier PASS/FAIL.
    """

    if not isinstance(projects, ProjectStore):
        raise TypeError("projects must be ProjectStore")
    if not isinstance(generation, SimulationReworkTaskGenerationResult):
        raise TypeError("generation must be SimulationReworkTaskGenerationResult")

    tasks = _current_tasks(projects, generation)
    root_runtime_run_id, consumed, reserved, registry_invalid = _retry_chain_state(
        projects, generation
    )

    if registry_invalid:
        return _decision(
            generation,
            root_runtime_run_id=root_runtime_run_id,
            consumed=consumed,
            reason=SimulationHumanGateReason.RETRY_REGISTRY_INVALID,
            tasks=tasks,
        )

    links = retry_module.UAVRunLinkStore(projects, Path(deployment_root).resolve())
    try:
        retry_module._validate_parent_link(links, generation)
    except retry_module.SimulationRetryConflictError:
        return _decision(
            generation,
            root_runtime_run_id=root_runtime_run_id,
            consumed=consumed,
            reason=SimulationHumanGateReason.PARENT_LINK_INVALID,
            tasks=tasks,
        )

    if not generation.bindings:
        return _decision(
            generation,
            root_runtime_run_id=root_runtime_run_id,
            consumed=consumed,
            reason=SimulationHumanGateReason.VERIFIER_PASS,
            tasks=tasks,
        )

    if any(record.status is TaskState.NEEDS_HUMAN for record in tasks):
        return _decision(
            generation,
            root_runtime_run_id=root_runtime_run_id,
            consumed=consumed,
            reason=SimulationHumanGateReason.REWORK_TASK_NEEDS_HUMAN,
            tasks=tasks,
        )

    if any(record.status in {TaskState.FAILED, TaskState.CANCELLED} for record in tasks):
        return _decision(
            generation,
            root_runtime_run_id=root_runtime_run_id,
            consumed=consumed,
            reason=SimulationHumanGateReason.REWORK_TASK_TERMINAL_FAILURE,
            tasks=tasks,
        )

    if any(record.status is not TaskState.DONE for record in tasks):
        return _decision(
            generation,
            root_runtime_run_id=root_runtime_run_id,
            consumed=consumed,
            reason=SimulationHumanGateReason.REWORK_IN_PROGRESS,
            tasks=tasks,
        )

    if reserved:
        return _decision(
            generation,
            root_runtime_run_id=root_runtime_run_id,
            consumed=consumed,
            reason=SimulationHumanGateReason.RETRY_CRASH_RESERVATION,
            tasks=tasks,
        )

    if consumed >= retry_module.MAX_SIMULATION_RETRY_ATTEMPTS:
        return _decision(
            generation,
            root_runtime_run_id=root_runtime_run_id,
            consumed=consumed,
            reason=SimulationHumanGateReason.RETRY_BUDGET_EXHAUSTED,
            tasks=tasks,
        )

    try:
        task_ids = retry_module._validate_rework_tasks_done(projects, generation)
        replacements = retry_module._replacement_sources(projects, generation, task_ids)
        retry_module._proposal_for_retry(projects, generation, replacements)
    except retry_module.SimulationRetryConflictError:
        return _decision(
            generation,
            root_runtime_run_id=root_runtime_run_id,
            consumed=consumed,
            reason=SimulationHumanGateReason.RETRY_PRECONDITION_BLOCKED,
            tasks=tasks,
        )

    return _decision(
        generation,
        root_runtime_run_id=root_runtime_run_id,
        consumed=consumed,
        reason=SimulationHumanGateReason.AUTO_RETRY_AVAILABLE,
        tasks=tasks,
    )
