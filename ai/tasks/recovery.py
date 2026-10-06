from __future__ import annotations

from dataclasses import dataclass, replace
import math
import sqlite3
import time

from ai.contracts.failures import FailureClass, FailureDescriptor
from ai.contracts.ids import CanonicalId, CanonicalIdError, IdKind
from ai.contracts.task_states import (
    InvalidTaskStateTransition,
    TaskState,
    TaskStateError,
    require_task_transition,
)
from ai.project.store import ProjectStore
from ai.tasks.lifecycle import (
    TaskLifecycleStore,
    TaskTransitionConflictError,
)
from ai.tasks.store import (
    TaskNotFoundError,
    TaskStore,
    _canonical_record_json,
    _parse_id,
    _parse_record_json,
    _registry_path,
    _reject_registry_path,
    initialize_task_registry,
    verify_task_registry,
)


TASK_INTERRUPTED_RECOVERY_CONTRACT_VERSION = "1.0"
MAX_INTERRUPTED_RECOVERIES_PER_SCAN = 4096


class TaskInterruptedRecoveryError(RuntimeError):
    """Base error for deterministic T5.9 interrupted-task recovery."""


class TaskInterruptedRecoveryConflictError(TaskInterruptedRecoveryError):
    """Raised when interrupted recovery cannot be applied safely."""


@dataclass(frozen=True)
class TaskInterruptedDecision:
    task_id: CanonicalId
    attempt: int
    observed_at_epoch_s: float
    started_at_epoch_s: float
    deadline_at_epoch_s: float
    deadline_passed: bool
    target_status: TaskState
    failure: FailureDescriptor

    def __post_init__(self) -> None:
        task = self.task_id
        if not isinstance(task, CanonicalId):
            try:
                task = CanonicalId.parse(task)
            except (CanonicalIdError, TypeError) as exc:
                raise TaskInterruptedRecoveryConflictError(
                    "decision task_id must be canonical"
                ) from exc
            object.__setattr__(self, "task_id", task)

        if task.kind is not IdKind.TASK:
            raise TaskInterruptedRecoveryConflictError(
                "decision task_id must identify a task"
            )
        if self.target_status is not TaskState.INTERRUPTED:
            raise TaskInterruptedRecoveryConflictError(
                "restart recovery target must be INTERRUPTED"
            )
        if (
            isinstance(self.attempt, bool)
            or not isinstance(self.attempt, int)
            or self.attempt < 1
        ):
            raise TaskInterruptedRecoveryConflictError(
                "decision attempt must be a positive integer"
            )
        for field in (
            "observed_at_epoch_s",
            "started_at_epoch_s",
            "deadline_at_epoch_s",
        ):
            value = getattr(self, field)
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(float(value))
                or float(value) < 0.0
            ):
                raise TaskInterruptedRecoveryConflictError(
                    f"decision {field} must be finite and non-negative"
                )
        if self.deadline_at_epoch_s <= self.started_at_epoch_s:
            raise TaskInterruptedRecoveryConflictError(
                "decision deadline must be after start"
            )
        if not isinstance(self.deadline_passed, bool):
            raise TaskInterruptedRecoveryConflictError(
                "decision deadline_passed must be boolean"
            )


@dataclass(frozen=True)
class TaskInterruptedRecoveryReport:
    project_id: CanonicalId
    observed_at_epoch_s: float
    recovered_count: int
    decisions: tuple[TaskInterruptedDecision, ...]

    def __post_init__(self) -> None:
        project = self.project_id
        if not isinstance(project, CanonicalId):
            try:
                project = CanonicalId.parse(project)
            except (CanonicalIdError, TypeError) as exc:
                raise TaskInterruptedRecoveryConflictError(
                    "report project_id must be canonical"
                ) from exc
            object.__setattr__(self, "project_id", project)

        if project.kind is not IdKind.PROJECT:
            raise TaskInterruptedRecoveryConflictError(
                "report project_id must identify a project"
            )
        if (
            isinstance(self.recovered_count, bool)
            or not isinstance(self.recovered_count, int)
            or self.recovered_count < 0
            or self.recovered_count != len(self.decisions)
        ):
            raise TaskInterruptedRecoveryConflictError(
                "report recovered_count is inconsistent"
            )


@dataclass(frozen=True)
class TaskInterruptedRoute:
    task_id: CanonicalId
    attempt: int
    max_attempts: int
    target_status: TaskState

    def __post_init__(self) -> None:
        if self.target_status not in {
            TaskState.BLOCKED,
            TaskState.NEEDS_HUMAN,
        }:
            raise TaskInterruptedRecoveryConflictError(
                "interrupted route target must be BLOCKED or NEEDS_HUMAN"
            )


def _now(raw: float | int | None) -> float:
    value = time.time() if raw is None else raw
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(float(value))
        or float(value) < 0.0
    ):
        raise TaskInterruptedRecoveryConflictError(
            "observed_at_epoch_s must be finite and non-negative"
        )
    return float(value)


def _failure(
    *,
    task_id: CanonicalId,
    attempt: int,
    started_at: float,
    deadline_at: float,
    observed_at: float,
) -> FailureDescriptor:
    return FailureDescriptor.from_dict(
        {
            "class": FailureClass.INFRA.value,
            "code": "INFRA_TASK_INTERRUPTED",
            "message": (
                "A task persisted as RUNNING across process restart and was "
                "recovered as INTERRUPTED."
            ),
            "details": {
                "task_id": task_id.value,
                "attempt": attempt,
                "started_at_epoch_s": started_at,
                "deadline_at_epoch_s": deadline_at,
                "observed_at_epoch_s": observed_at,
                "deadline_passed": observed_at >= deadline_at,
            },
        }
    )


class TaskInterruptedRecoveryStore:
    """Recover persisted RUNNING tasks after an unexpected process stop.

    Frozen rule:
        persisted RUNNING after restart -> INTERRUPTED

    The already-consumed attempt remains unchanged. T5.8 execution-timing
    metadata is cleared atomically with the state transition because the prior
    execution process no longer owns a live timeout window.

    Recovery does not silently retry. Explicit routing is separate:
        INTERRUPTED + budget remaining -> BLOCKED
        INTERRUPTED + exhausted budget -> NEEDS_HUMAN

    BLOCKED deliberately hands control back to T5.5 before a future retry can
    become READY.

    Restart interruption takes precedence over timeout classification for a
    task discovered as RUNNING during restart recovery. The report preserves
    whether the old deadline had already passed, but state still becomes
    INTERRUPTED exactly as required by the frozen baseline.
    """

    def __init__(self, project_store: ProjectStore) -> None:
        if not isinstance(project_store, ProjectStore):
            raise TypeError("project_store must be ProjectStore")
        self._tasks = TaskStore(project_store)
        self._lifecycle = TaskLifecycleStore(project_store)

    def recover_project(
        self,
        project_id: CanonicalId | str,
        *,
        observed_at_epoch_s: float | int | None = None,
    ) -> TaskInterruptedRecoveryReport:
        observed = _now(observed_at_epoch_s)
        handle = self._tasks._open_handle(project_id)
        path = _registry_path(handle)

        if not path.exists():
            return TaskInterruptedRecoveryReport(
                project_id=handle.project_id,
                observed_at_epoch_s=observed,
                recovered_count=0,
                decisions=(),
            )

        _reject_registry_path(path)
        initialize_task_registry(path)

        connection = sqlite3.connect(path)
        try:
            verify_task_registry(connection)
            connection.execute("BEGIN IMMEDIATE")
            try:
                rows = connection.execute(
                    """
                    SELECT task_id, status, record_json,
                           execution_attempt,
                           started_at_epoch_s,
                           deadline_at_epoch_s
                    FROM tasks
                    WHERE project_id=? AND status=?
                    ORDER BY task_id
                    LIMIT ?
                    """,
                    (
                        handle.project_id.value,
                        TaskState.RUNNING.value,
                        MAX_INTERRUPTED_RECOVERIES_PER_SCAN + 1,
                    ),
                ).fetchall()

                if len(rows) > MAX_INTERRUPTED_RECOVERIES_PER_SCAN:
                    raise TaskInterruptedRecoveryConflictError(
                        "restart recovery exceeds "
                        "MAX_INTERRUPTED_RECOVERIES_PER_SCAN"
                    )

                decisions: list[TaskInterruptedDecision] = []
                for (
                    task_id_raw,
                    status_raw,
                    record_json,
                    execution_attempt,
                    started_at,
                    deadline_at,
                ) in rows:
                    record = _parse_record_json(record_json)
                    if (
                        record.task_id.value != task_id_raw
                        or record.project_id != handle.project_id
                        or status_raw != TaskState.RUNNING.value
                        or record.status is not TaskState.RUNNING
                    ):
                        raise TaskInterruptedRecoveryConflictError(
                            "restart recovery encountered inconsistent RUNNING task"
                        )
                    if (
                        execution_attempt != record.attempt
                        or started_at is None
                        or deadline_at is None
                    ):
                        raise TaskInterruptedRecoveryConflictError(
                            "restart recovery encountered inconsistent timing metadata"
                        )

                    try:
                        target = require_task_transition(
                            record.status,
                            TaskState.INTERRUPTED,
                        )
                    except (
                        InvalidTaskStateTransition,
                        TaskStateError,
                    ) as exc:
                        raise TaskInterruptedRecoveryConflictError(
                            "RUNNING task cannot recover to INTERRUPTED"
                        ) from exc

                    updated = replace(
                        record,
                        status=target,
                    )
                    updated_json = _canonical_record_json(updated)

                    connection.execute(
                        """
                        UPDATE tasks
                        SET status=?,
                            record_json=?,
                            execution_attempt=NULL,
                            started_at_epoch_s=NULL,
                            deadline_at_epoch_s=NULL
                        WHERE task_id=?
                          AND status=?
                          AND record_json=?
                          AND execution_attempt=?
                          AND started_at_epoch_s=?
                          AND deadline_at_epoch_s=?
                        """,
                        (
                            updated.status.value,
                            updated_json,
                            record.task_id.value,
                            record.status.value,
                            record_json,
                            record.attempt,
                            started_at,
                            deadline_at,
                        ),
                    )
                    if connection.execute(
                        "SELECT changes()"
                    ).fetchone()[0] != 1:
                        raise TaskInterruptedRecoveryConflictError(
                            "task changed during interrupted recovery"
                        )

                    decisions.append(
                        TaskInterruptedDecision(
                            task_id=record.task_id,
                            attempt=record.attempt,
                            observed_at_epoch_s=observed,
                            started_at_epoch_s=float(started_at),
                            deadline_at_epoch_s=float(deadline_at),
                            deadline_passed=(
                                observed >= float(deadline_at)
                            ),
                            target_status=target,
                            failure=_failure(
                                task_id=record.task_id,
                                attempt=record.attempt,
                                started_at=float(started_at),
                                deadline_at=float(deadline_at),
                                observed_at=observed,
                            ),
                        )
                    )

                connection.commit()

                return TaskInterruptedRecoveryReport(
                    project_id=handle.project_id,
                    observed_at_epoch_s=observed,
                    recovered_count=len(decisions),
                    decisions=tuple(decisions),
                )
            except Exception:
                connection.rollback()
                raise
        finally:
            connection.close()

    def route_interrupted(
        self,
        project_id: CanonicalId | str,
        task_id: CanonicalId | str,
    ) -> TaskInterruptedRoute:
        current = self._tasks.open_task(project_id, task_id)
        if current.status is not TaskState.INTERRUPTED:
            raise TaskInterruptedRecoveryConflictError(
                "interrupted routing requires INTERRUPTED status"
            )

        target = (
            TaskState.NEEDS_HUMAN
            if current.attempt >= current.max_attempts
            else TaskState.BLOCKED
        )

        try:
            updated = self._lifecycle.transition_task(
                project_id,
                current.task_id,
                target,
            )
        except TaskTransitionConflictError as exc:
            raise TaskInterruptedRecoveryConflictError(
                f"INTERRUPTED task could not be routed: {exc}"
            ) from exc

        if updated.attempt != current.attempt:
            raise TaskInterruptedRecoveryConflictError(
                "interrupted routing unexpectedly changed attempt"
            )

        return TaskInterruptedRoute(
            task_id=current.task_id,
            attempt=current.attempt,
            max_attempts=current.max_attempts,
            target_status=updated.status,
        )
