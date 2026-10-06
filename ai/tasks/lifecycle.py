from __future__ import annotations

from dataclasses import replace
import math
import sqlite3
import time

from ai.contracts.ids import CanonicalId
from ai.contracts.task_states import (
    InvalidTaskStateTransition,
    TaskState,
    TaskStateError,
    allowed_task_transitions,
    require_task_transition,
)
from ai.project.store import ProjectStore
from ai.tasks.schema import TaskRecord
from ai.tasks.store import (
    TaskNotFoundError,
    TaskStore,
    TaskStoreConflictError,
    _canonical_record_json,
    _parse_id,
    _parse_record_json,
    _registry_path,
    _reject_registry_path,
    initialize_task_registry,
    verify_task_registry,
)
from ai.contracts.ids import IdKind


TASK_LIFECYCLE_CONTRACT_VERSION = "1.2"


TASK_TIMEOUT_S = 300.0


def _epoch_seconds(raw: float | int | None) -> float:
    value = time.time() if raw is None else raw
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(float(value))
        or float(value) < 0.0
    ):
        raise TaskTransitionConflictError(
            "now_epoch_s must be a finite non-negative number"
        )
    return float(value)


class TaskLifecycleError(RuntimeError):
    """Base error for persistent T5.2 task-state transitions."""


class TaskTransitionConflictError(TaskLifecycleError):
    """Raised when a requested transition is illegal or storage changed."""


class TaskLifecycleStore:
    """Persistent executor for the frozen Phase-1 task state machine.

    T5.7 extends this executor so the legal ASSIGNED -> RUNNING transition
    atomically increments `attempt` by exactly one. No other state transition
    changes the attempt counter. Dependency readiness, queueing policy, timeout,
    interrupted recovery, cancellation orchestration, and concurrency remain
    separate Phase-5 packages. T5.8 additionally arms the frozen 300-second
    execution deadline atomically when a task enters RUNNING and clears that
    runtime timing metadata whenever RUNNING ends.

    Generic state transitions may target any edge already present in the
    frozen C1.2 contract, including INTERRUPTED/CANCELLED. T5.9/T5.10 own
    automatic recovery/cancellation policy. T5.11 makes QUEUED -> ASSIGNED
    a globally guarded transition so the frozen max_parallel_agents=1 limit
    cannot be bypassed through this lifecycle API.
    """

    def __init__(self, project_store: ProjectStore) -> None:
        if not isinstance(project_store, ProjectStore):
            raise TypeError("project_store must be ProjectStore")
        self._project_store = project_store
        self._tasks = TaskStore(project_store)

    def allowed_transitions(
        self,
        project_id: CanonicalId | str,
        task_id: CanonicalId | str,
    ) -> frozenset[TaskState]:
        current = self._tasks.open_task(project_id, task_id)
        return allowed_task_transitions(current.status)

    def transition_task(
        self,
        project_id: CanonicalId | str,
        task_id: CanonicalId | str,
        target: TaskState | str,
        *,
        now_epoch_s: float | int | None = None,
    ) -> TaskRecord:
        try:
            target_state = TaskState(target) if isinstance(target, str) else target
            # require_task_transition performs the strict parse below once the
            # persisted current state is known. This early check makes bad
            # target types fail before registry mutation.
            if not isinstance(target_state, TaskState):
                raise TaskStateError("target must be TaskState")
        except (ValueError, TaskStateError, TypeError) as exc:
            raise TaskTransitionConflictError(
                "target is not a canonical task state"
            ) from exc

        if target_state is TaskState.ASSIGNED:
            from ai.tasks.concurrency import (
                TaskConcurrencyError,
                TaskConcurrencyScheduler,
            )

            try:
                assignment = TaskConcurrencyScheduler(
                    self._project_store
                ).assign_specific(
                    project_id,
                    task_id,
                )
            except TaskConcurrencyError as exc:
                raise TaskTransitionConflictError(
                    f"ASSIGNED transition rejected by concurrency gate: {exc}"
                ) from exc
            return assignment.task

        handle = self._tasks._open_handle(project_id)
        task = _parse_id(task_id, kind=IdKind.TASK, field="task_id")
        path = _registry_path(handle)
        if not path.exists():
            raise TaskNotFoundError("task registry does not exist")
        _reject_registry_path(path)
        initialize_task_registry(path)

        connection = sqlite3.connect(path)
        try:
            verify_task_registry(connection)
            connection.execute("BEGIN IMMEDIATE")
            try:
                row = connection.execute(
                    "SELECT project_id, status, record_json, execution_attempt, started_at_epoch_s, deadline_at_epoch_s FROM tasks WHERE task_id=?",
                    (task.value,),
                ).fetchone()
                if row is None:
                    raise TaskNotFoundError(f"task does not exist: {task.value}")

                current = _parse_record_json(row[2])
                if current.task_id != task:
                    raise TaskStoreConflictError("stored task identity mismatch")
                if (
                    current.project_id != handle.project_id
                    or row[0] != handle.project_id.value
                ):
                    raise TaskStoreConflictError("stored task project mismatch")
                if row[1] != current.status.value:
                    raise TaskStoreConflictError("task registry status index mismatch")

                try:
                    canonical_target = require_task_transition(
                        current.status,
                        target_state,
                    )
                except (InvalidTaskStateTransition, TaskStateError) as exc:
                    raise TaskTransitionConflictError(str(exc)) from exc

                execution_attempt = row[3]
                started_at = row[4]
                deadline_at = row[5]

                if current.status is TaskState.RUNNING:
                    if (
                        execution_attempt != current.attempt
                        or started_at is None
                        or deadline_at is None
                    ):
                        raise TaskTransitionConflictError(
                            "RUNNING task timeout metadata is inconsistent"
                        )

                if canonical_target is TaskState.RUNNING:
                    if current.attempt >= current.max_attempts:
                        raise TaskTransitionConflictError(
                            "task attempt budget exhausted; RUNNING is forbidden"
                        )
                    started = _epoch_seconds(now_epoch_s)
                    updated = replace(
                        current,
                        status=canonical_target,
                        attempt=current.attempt + 1,
                    )
                    next_execution_attempt = updated.attempt
                    next_started_at = started
                    next_deadline_at = started + TASK_TIMEOUT_S
                else:
                    updated = replace(
                        current,
                        status=canonical_target,
                    )
                    if current.status is TaskState.RUNNING:
                        next_execution_attempt = None
                        next_started_at = None
                        next_deadline_at = None
                    else:
                        next_execution_attempt = execution_attempt
                        next_started_at = started_at
                        next_deadline_at = deadline_at

                updated_json = _canonical_record_json(updated)

                connection.execute(
                    """
                    UPDATE tasks
                    SET status=?, record_json=?,
                        execution_attempt=?, started_at_epoch_s=?, deadline_at_epoch_s=?
                    WHERE task_id=? AND status=? AND record_json=?
                    """,
                    (
                        updated.status.value,
                        updated_json,
                        next_execution_attempt,
                        next_started_at,
                        next_deadline_at,
                        task.value,
                        current.status.value,
                        row[2],
                    ),
                )
                if connection.execute("SELECT changes()").fetchone()[0] != 1:
                    raise TaskTransitionConflictError(
                        "task changed during state transition"
                    )
                connection.commit()
                return updated
            except Exception:
                connection.rollback()
                raise
        finally:
            connection.close()
