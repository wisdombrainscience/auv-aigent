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
from ai.tasks.lifecycle import TASK_TIMEOUT_S
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


TASK_TIMEOUT_CONTRACT_VERSION = "1.0"
MAX_TIMEOUT_EXPIRATIONS_PER_SCAN = 4096


class TaskTimeoutError(RuntimeError):
    """Base error for T5.8 execution-timeout enforcement."""


class TaskTimeoutConflictError(TaskTimeoutError):
    """Raised when timeout metadata/state cannot be trusted."""


@dataclass(frozen=True)
class TaskTimeoutWindow:
    task_id: CanonicalId
    attempt: int
    started_at_epoch_s: float
    deadline_at_epoch_s: float
    timeout_s: float

    @property
    def expired_at(self) -> float:
        return self.deadline_at_epoch_s


@dataclass(frozen=True)
class TaskTimeoutDecision:
    task_id: CanonicalId
    attempt: int
    observed_at_epoch_s: float
    target_status: TaskState
    failure: FailureDescriptor


@dataclass(frozen=True)
class TaskTimeoutReport:
    project_id: CanonicalId
    observed_at_epoch_s: float
    expired_count: int
    decisions: tuple[TaskTimeoutDecision, ...]


def _now(raw: float | int | None) -> float:
    value = time.time() if raw is None else raw
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(float(value))
        or float(value) < 0.0
    ):
        raise TaskTimeoutConflictError(
            "now_epoch_s must be a finite non-negative number"
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
            "class": FailureClass.TASK.value,
            "code": "TASK_TIMEOUT",
            "message": "Task execution exceeded the frozen timeout limit.",
            "details": {
                "task_id": task_id.value,
                "attempt": attempt,
                "timeout_s": TASK_TIMEOUT_S,
                "started_at_epoch_s": started_at,
                "deadline_at_epoch_s": deadline_at,
                "observed_at_epoch_s": observed_at,
            },
        }
    )


class TaskTimeoutStore:
    """Persisted deadline inspection and deterministic timeout enforcement.

    T5.8 uses the frozen default `task.timeout_s = 300` exactly. A task that
    reaches this hard limit transitions RUNNING -> NEEDS_HUMAN, matching the
    frozen anti-loop rule that a reached limit requires human intervention.

    There is no background thread, sleep loop, automatic retry, or INTERRUPTED
    recovery here. T5.9 remains solely responsible for unexpected restart /
    process-interruption recovery.
    """

    def __init__(self, project_store: ProjectStore) -> None:
        if not isinstance(project_store, ProjectStore):
            raise TypeError("project_store must be ProjectStore")
        self._tasks = TaskStore(project_store)

    def window(
        self,
        project_id: CanonicalId | str,
        task_id: CanonicalId | str,
    ) -> TaskTimeoutWindow:
        handle = self._tasks._open_handle(project_id)
        task = _parse_id(task_id, kind=IdKind.TASK, field="task_id")
        path = _registry_path(handle)
        if not path.exists():
            raise TaskNotFoundError("task registry does not exist")
        _reject_registry_path(path)
        initialize_task_registry(path)

        with sqlite3.connect(path) as connection:
            verify_task_registry(connection)
            row = connection.execute(
                """
                SELECT status, record_json,
                       execution_attempt, started_at_epoch_s, deadline_at_epoch_s
                FROM tasks
                WHERE task_id=? AND project_id=?
                """,
                (task.value, handle.project_id.value),
            ).fetchone()

        if row is None:
            raise TaskNotFoundError(f"task does not exist: {task.value}")
        record = _parse_record_json(row[1])
        if record.status is not TaskState.RUNNING:
            raise TaskTimeoutConflictError(
                "timeout window exists only for RUNNING tasks"
            )
        attempt, started_at, deadline_at = row[2], row[3], row[4]
        if attempt != record.attempt or started_at is None or deadline_at is None:
            raise TaskTimeoutConflictError(
                "RUNNING task timeout metadata is inconsistent"
            )
        return TaskTimeoutWindow(
            task_id=record.task_id,
            attempt=attempt,
            started_at_epoch_s=float(started_at),
            deadline_at_epoch_s=float(deadline_at),
            timeout_s=TASK_TIMEOUT_S,
        )

    def remaining_seconds(
        self,
        project_id: CanonicalId | str,
        task_id: CanonicalId | str,
        *,
        now_epoch_s: float | int | None = None,
    ) -> float:
        window = self.window(project_id, task_id)
        observed = _now(now_epoch_s)
        return max(0.0, window.deadline_at_epoch_s - observed)

    def expire_overdue(
        self,
        project_id: CanonicalId | str,
        *,
        now_epoch_s: float | int | None = None,
    ) -> TaskTimeoutReport:
        observed = _now(now_epoch_s)
        handle = self._tasks._open_handle(project_id)
        path = _registry_path(handle)
        if not path.exists():
            return TaskTimeoutReport(
                project_id=handle.project_id,
                observed_at_epoch_s=observed,
                expired_count=0,
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
                           execution_attempt, started_at_epoch_s, deadline_at_epoch_s
                    FROM tasks
                    WHERE project_id=? AND status=? AND deadline_at_epoch_s <= ?
                    ORDER BY deadline_at_epoch_s, task_id
                    LIMIT ?
                    """,
                    (
                        handle.project_id.value,
                        TaskState.RUNNING.value,
                        observed,
                        MAX_TIMEOUT_EXPIRATIONS_PER_SCAN + 1,
                    ),
                ).fetchall()

                if len(rows) > MAX_TIMEOUT_EXPIRATIONS_PER_SCAN:
                    raise TaskTimeoutConflictError(
                        "timeout scan exceeds MAX_TIMEOUT_EXPIRATIONS_PER_SCAN"
                    )

                decisions: list[TaskTimeoutDecision] = []
                for (
                    task_id_raw, status_raw, record_json,
                    execution_attempt, started_at, deadline_at,
                ) in rows:
                    record = _parse_record_json(record_json)
                    if (
                        record.task_id.value != task_id_raw
                        or record.project_id != handle.project_id
                        or status_raw != TaskState.RUNNING.value
                        or record.status is not TaskState.RUNNING
                    ):
                        raise TaskTimeoutConflictError(
                            "timeout scan encountered inconsistent RUNNING task"
                        )
                    if (
                        execution_attempt != record.attempt
                        or started_at is None
                        or deadline_at is None
                    ):
                        raise TaskTimeoutConflictError(
                            "timeout scan encountered inconsistent timing metadata"
                        )

                    try:
                        target = require_task_transition(
                            record.status,
                            TaskState.NEEDS_HUMAN,
                        )
                    except (InvalidTaskStateTransition, TaskStateError) as exc:
                        raise TaskTimeoutConflictError(
                            "timed-out RUNNING task cannot enter NEEDS_HUMAN"
                        ) from exc

                    updated = replace(record, status=target)
                    updated_json = _canonical_record_json(updated)
                    connection.execute(
                        """
                        UPDATE tasks
                        SET status=?, record_json=?,
                            execution_attempt=NULL,
                            started_at_epoch_s=NULL,
                            deadline_at_epoch_s=NULL
                        WHERE task_id=?
                          AND status=?
                          AND record_json=?
                          AND execution_attempt=?
                          AND deadline_at_epoch_s=?
                        """,
                        (
                            updated.status.value,
                            updated_json,
                            record.task_id.value,
                            record.status.value,
                            record_json,
                            record.attempt,
                            deadline_at,
                        ),
                    )
                    if connection.execute(
                        "SELECT changes()"
                    ).fetchone()[0] != 1:
                        raise TaskTimeoutConflictError(
                            "task changed during timeout enforcement"
                        )

                    decisions.append(
                        TaskTimeoutDecision(
                            task_id=record.task_id,
                            attempt=record.attempt,
                            observed_at_epoch_s=observed,
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
                return TaskTimeoutReport(
                    project_id=handle.project_id,
                    observed_at_epoch_s=observed,
                    expired_count=len(decisions),
                    decisions=tuple(decisions),
                )
            except Exception:
                connection.rollback()
                raise
        finally:
            connection.close()
