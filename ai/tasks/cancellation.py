from __future__ import annotations

from dataclasses import dataclass, replace
import sqlite3

from ai.contracts.ids import CanonicalId, CanonicalIdError, IdKind
from ai.contracts.task_states import (
    InvalidTaskStateTransition,
    TaskState,
    TaskStateError,
    require_task_transition,
)
from ai.project.store import ProjectStore
from ai.tasks.dag import TaskDAGConflictError, TaskDAGCycleError, TaskDAGValidator
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


TASK_CANCELLATION_CONTRACT_VERSION = "1.0"
MAX_CANCELLATION_TARGETS = 4096

# CANCELLED is idempotently cancel-safe. DONE and FAILED are immutable terminal
# outcomes and are therefore intentionally excluded.
CANCELLABLE_TASK_STATES = frozenset(
    state
    for state in TaskState
    if state not in {TaskState.DONE, TaskState.FAILED}
)


class TaskCancellationError(RuntimeError):
    """Base error for deterministic T5.10 task cancellation."""


class TaskCancellationConflictError(TaskCancellationError):
    """Raised when cancellation cannot be applied safely."""


@dataclass(frozen=True)
class TaskCancellationDecision:
    task_id: CanonicalId
    previous_status: TaskState
    resolved_status: TaskState
    changed: bool
    terminal_skipped: bool

    def __post_init__(self) -> None:
        task = self.task_id
        if not isinstance(task, CanonicalId):
            try:
                task = CanonicalId.parse(task)
            except (CanonicalIdError, TypeError) as exc:
                raise TaskCancellationConflictError(
                    "cancellation decision task_id must be canonical"
                ) from exc
            object.__setattr__(self, "task_id", task)
        if task.kind is not IdKind.TASK:
            raise TaskCancellationConflictError(
                "cancellation decision task_id must identify a task"
            )
        if not isinstance(self.previous_status, TaskState):
            raise TaskCancellationConflictError(
                "cancellation decision previous_status is invalid"
            )
        if not isinstance(self.resolved_status, TaskState):
            raise TaskCancellationConflictError(
                "cancellation decision resolved_status is invalid"
            )
        if not isinstance(self.changed, bool):
            raise TaskCancellationConflictError(
                "cancellation decision changed must be boolean"
            )
        if not isinstance(self.terminal_skipped, bool):
            raise TaskCancellationConflictError(
                "cancellation decision terminal_skipped must be boolean"
            )
        if self.terminal_skipped:
            if self.previous_status not in {TaskState.DONE, TaskState.FAILED}:
                raise TaskCancellationConflictError(
                    "only DONE/FAILED descendants may be terminal-skipped"
                )
            if self.resolved_status is not self.previous_status or self.changed:
                raise TaskCancellationConflictError(
                    "terminal-skipped cancellation decision is inconsistent"
                )
        elif self.resolved_status is not TaskState.CANCELLED:
            raise TaskCancellationConflictError(
                "non-skipped cancellation decision must resolve to CANCELLED"
            )


@dataclass(frozen=True)
class TaskCancellationReport:
    project_id: CanonicalId
    root_task_id: CanonicalId
    cascade_dependents: bool
    targeted_count: int
    cancelled_count: int
    already_cancelled_count: int
    terminal_skipped_count: int
    decisions: tuple[TaskCancellationDecision, ...]

    def __post_init__(self) -> None:
        for field, kind in (
            ("project_id", IdKind.PROJECT),
            ("root_task_id", IdKind.TASK),
        ):
            value = getattr(self, field)
            if not isinstance(value, CanonicalId):
                try:
                    value = CanonicalId.parse(value)
                except (CanonicalIdError, TypeError) as exc:
                    raise TaskCancellationConflictError(
                        f"cancellation report {field} must be canonical"
                    ) from exc
                object.__setattr__(self, field, value)
            if value.kind is not kind:
                raise TaskCancellationConflictError(
                    f"cancellation report {field} has wrong kind"
                )
        if not isinstance(self.cascade_dependents, bool):
            raise TaskCancellationConflictError(
                "cascade_dependents must be boolean"
            )
        for field in (
            "targeted_count",
            "cancelled_count",
            "already_cancelled_count",
            "terminal_skipped_count",
        ):
            value = getattr(self, field)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise TaskCancellationConflictError(
                    f"cancellation report {field} must be non-negative"
                )
        if self.targeted_count != len(self.decisions):
            raise TaskCancellationConflictError(
                "targeted_count does not match decisions"
            )
        if (
            self.cancelled_count
            + self.already_cancelled_count
            + self.terminal_skipped_count
            != self.targeted_count
        ):
            raise TaskCancellationConflictError(
                "cancellation report counters do not cover all targets"
            )


class TaskCancellationStore:
    """Explicit, bounded, deterministic task cancellation.

    Exact cancellation changes one non-final task to CANCELLED. CANCELLED is
    idempotent. DONE and FAILED are immutable terminal outcomes and cannot be
    rewritten by cancellation.

    Optional dependent cascade follows T5.3 reverse dependency edges after a
    successful T5.4 DAG validation. Because task dependencies are hard
    prerequisites, a cancelled prerequisite cannot be satisfied by T5.5; the
    caller may explicitly request cancellation of its transitive dependents.
    Cascade is never implicit: `cascade_dependents=False` is the default.

    All selected task state changes for one call are committed in one Task
    Registry transaction. RUNNING cancellation clears T5.8 execution timing
    metadata atomically and preserves the already-consumed attempt.

    T5.10 does not signal/kill a worker process, because Agent execution and
    worker ownership are not introduced until later phases. T5.11 owns only
    concurrency limits, not cancellation semantics.
    """

    def __init__(self, project_store: ProjectStore) -> None:
        if not isinstance(project_store, ProjectStore):
            raise TypeError("project_store must be ProjectStore")
        self._project_store = project_store
        self._tasks = TaskStore(project_store)
        self._dag = TaskDAGValidator(project_store)

    @staticmethod
    def _descendant_targets(
        connection: sqlite3.Connection,
        root_task_id: str,
    ) -> tuple[str, ...]:
        rows = connection.execute(
            """
            SELECT dependency_task_id, child_task_id
            FROM task_dependency_edges
            ORDER BY dependency_task_id, child_task_id
            """
        ).fetchall()
        reverse: dict[str, list[str]] = {}
        for dependency, child in rows:
            reverse.setdefault(dependency, []).append(child)

        seen = {root_task_id}
        order = [root_task_id]
        queue = [root_task_id]
        cursor = 0
        while cursor < len(queue):
            current = queue[cursor]
            cursor += 1
            for child in reverse.get(current, ()):
                if child in seen:
                    continue
                seen.add(child)
                order.append(child)
                queue.append(child)
                if len(order) > MAX_CANCELLATION_TARGETS:
                    raise TaskCancellationConflictError(
                        "cancellation target set exceeds MAX_CANCELLATION_TARGETS"
                    )
        return tuple(order)

    def cancel_task(
        self,
        project_id: CanonicalId | str,
        task_id: CanonicalId | str,
        *,
        cascade_dependents: bool = False,
    ) -> TaskCancellationReport:
        if not isinstance(cascade_dependents, bool):
            raise TypeError("cascade_dependents must be boolean")

        handle = self._tasks._open_handle(project_id)
        root = _parse_id(task_id, kind=IdKind.TASK, field="task_id")

        # Only cascades require a valid DAG. Exact cancellation remains
        # available as an escape hatch even when a project graph is invalid.
        dag_ids: tuple[str, ...] | None = None
        if cascade_dependents:
            try:
                dag_report = self._dag.validate_project(handle.project_id)
            except (TaskDAGConflictError, TaskDAGCycleError) as exc:
                raise TaskCancellationConflictError(
                    f"dependent cancellation requires valid task DAG: {exc}"
                ) from exc
            dag_ids = tuple(
                sorted(item.value for item in dag_report.topological_order)
            )

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
                root_row = connection.execute(
                    """
                    SELECT project_id, status, record_json,
                           execution_attempt, started_at_epoch_s,
                           deadline_at_epoch_s
                    FROM tasks
                    WHERE task_id=?
                    """,
                    (root.value,),
                ).fetchone()
                if root_row is None:
                    raise TaskNotFoundError(
                        f"task does not exist: {root.value}"
                    )

                root_record = _parse_record_json(root_row[2])
                if (
                    root_record.task_id != root
                    or root_record.project_id != handle.project_id
                    or root_row[0] != handle.project_id.value
                    or root_row[1] != root_record.status.value
                ):
                    raise TaskCancellationConflictError(
                        "root task registry identity/status is inconsistent"
                    )
                if root_record.status in {TaskState.DONE, TaskState.FAILED}:
                    raise TaskCancellationConflictError(
                        "DONE/FAILED task outcome cannot be cancelled"
                    )

                if cascade_dependents:
                    current_ids = tuple(
                        row[0]
                        for row in connection.execute(
                            "SELECT task_id FROM tasks ORDER BY task_id"
                        ).fetchall()
                    )
                    if current_ids != dag_ids:
                        raise TaskCancellationConflictError(
                            "task graph changed after DAG validation"
                        )
                    target_ids = self._descendant_targets(
                        connection,
                        root.value,
                    )
                else:
                    target_ids = (root.value,)

                if len(target_ids) > MAX_CANCELLATION_TARGETS:
                    raise TaskCancellationConflictError(
                        "cancellation target set exceeds MAX_CANCELLATION_TARGETS"
                    )

                decisions: list[TaskCancellationDecision] = []
                cancelled_count = 0
                already_cancelled_count = 0
                terminal_skipped_count = 0

                for target_id in target_ids:
                    row = connection.execute(
                        """
                        SELECT project_id, status, record_json,
                               execution_attempt, started_at_epoch_s,
                               deadline_at_epoch_s
                        FROM tasks
                        WHERE task_id=?
                        """,
                        (target_id,),
                    ).fetchone()
                    if row is None:
                        raise TaskCancellationConflictError(
                            "cancellation target disappeared during transaction"
                        )
                    current = _parse_record_json(row[2])
                    if (
                        current.task_id.value != target_id
                        or current.project_id != handle.project_id
                        or row[0] != handle.project_id.value
                        or row[1] != current.status.value
                    ):
                        raise TaskCancellationConflictError(
                            "cancellation target registry identity/status mismatch"
                        )

                    if current.status is TaskState.CANCELLED:
                        already_cancelled_count += 1
                        decisions.append(
                            TaskCancellationDecision(
                                task_id=current.task_id,
                                previous_status=current.status,
                                resolved_status=TaskState.CANCELLED,
                                changed=False,
                                terminal_skipped=False,
                            )
                        )
                        continue

                    if current.status in {TaskState.DONE, TaskState.FAILED}:
                        # Root DONE/FAILED was rejected above. Historical
                        # terminal descendants are never rewritten.
                        terminal_skipped_count += 1
                        decisions.append(
                            TaskCancellationDecision(
                                task_id=current.task_id,
                                previous_status=current.status,
                                resolved_status=current.status,
                                changed=False,
                                terminal_skipped=True,
                            )
                        )
                        continue

                    try:
                        cancelled = require_task_transition(
                            current.status,
                            TaskState.CANCELLED,
                        )
                    except (
                        InvalidTaskStateTransition,
                        TaskStateError,
                    ) as exc:
                        raise TaskCancellationConflictError(
                            "task cannot transition to CANCELLED: "
                            f"{current.task_id.value}:{current.status.value}"
                        ) from exc

                    updated = replace(
                        current,
                        status=cancelled,
                    )
                    updated_json = _canonical_record_json(updated)

                    if current.status is TaskState.RUNNING:
                        execution_attempt = row[3]
                        started_at = row[4]
                        deadline_at = row[5]
                        if (
                            execution_attempt != current.attempt
                            or started_at is None
                            or deadline_at is None
                        ):
                            raise TaskCancellationConflictError(
                                "RUNNING cancellation target has invalid timing metadata"
                            )
                        next_execution_attempt = None
                        next_started_at = None
                        next_deadline_at = None
                    else:
                        if any(value is not None for value in row[3:6]):
                            raise TaskCancellationConflictError(
                                "non-RUNNING cancellation target retains timing metadata"
                            )
                        next_execution_attempt = None
                        next_started_at = None
                        next_deadline_at = None

                    connection.execute(
                        """
                        UPDATE tasks
                        SET status=?, record_json=?,
                            execution_attempt=?,
                            started_at_epoch_s=?,
                            deadline_at_epoch_s=?
                        WHERE task_id=?
                          AND status=?
                          AND record_json=?
                        """,
                        (
                            TaskState.CANCELLED.value,
                            updated_json,
                            next_execution_attempt,
                            next_started_at,
                            next_deadline_at,
                            current.task_id.value,
                            current.status.value,
                            row[2],
                        ),
                    )
                    if connection.execute(
                        "SELECT changes()"
                    ).fetchone()[0] != 1:
                        raise TaskCancellationConflictError(
                            "task changed during cancellation"
                        )

                    cancelled_count += 1
                    decisions.append(
                        TaskCancellationDecision(
                            task_id=current.task_id,
                            previous_status=current.status,
                            resolved_status=TaskState.CANCELLED,
                            changed=True,
                            terminal_skipped=False,
                        )
                    )

                connection.commit()
                return TaskCancellationReport(
                    project_id=handle.project_id,
                    root_task_id=root,
                    cascade_dependents=cascade_dependents,
                    targeted_count=len(target_ids),
                    cancelled_count=cancelled_count,
                    already_cancelled_count=already_cancelled_count,
                    terminal_skipped_count=terminal_skipped_count,
                    decisions=tuple(decisions),
                )
            except Exception:
                connection.rollback()
                raise
        finally:
            connection.close()
