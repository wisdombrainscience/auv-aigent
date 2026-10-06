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
from ai.tasks.readiness import TaskReadinessConflictError, TaskReadinessResolver
from ai.tasks.schema import TaskRecord
from ai.tasks.store import (
    TaskStore,
    _canonical_record_json,
    _parse_record_json,
    _registry_path,
    _reject_registry_path,
    initialize_task_registry,
    verify_task_registry,
)


TASK_PRIORITY_QUEUE_CONTRACT_VERSION = "1.1"
PRIORITY_POLICY = "HIGHER_NUMERIC_VALUE_FIRST"
PRIORITY_TIE_BREAKER = "CANONICAL_TASK_ID_ASC"
MAX_PRIORITY_QUEUE_ITEMS = 4096


class TaskPriorityQueueError(RuntimeError):
    """Base error for deterministic T5.6 priority queue behavior."""


class TaskPriorityQueueConflictError(TaskPriorityQueueError):
    """Raised when a queue cannot be trusted or updated safely."""


@dataclass(frozen=True, order=True)
class TaskQueueItem:
    task_id: CanonicalId
    priority: int

    def __post_init__(self) -> None:
        task = self.task_id
        if not isinstance(task, CanonicalId):
            try:
                task = CanonicalId.parse(task)
            except (CanonicalIdError, TypeError) as exc:
                raise TaskPriorityQueueConflictError(
                    "queue item task_id must be canonical"
                ) from exc
            object.__setattr__(self, "task_id", task)
        if task.kind is not IdKind.TASK:
            raise TaskPriorityQueueConflictError(
                "queue item task_id must identify a task"
            )
        if isinstance(self.priority, bool) or not isinstance(self.priority, int):
            raise TaskPriorityQueueConflictError(
                "queue item priority must be an integer"
            )
        if self.priority < 0:
            raise TaskPriorityQueueConflictError(
                "queue item priority must be non-negative"
            )


@dataclass(frozen=True)
class TaskPriorityQueueReport:
    project_id: CanonicalId
    newly_queued_count: int
    queued_count: int
    items: tuple[TaskQueueItem, ...]

    def __post_init__(self) -> None:
        project = self.project_id
        if not isinstance(project, CanonicalId):
            try:
                project = CanonicalId.parse(project)
            except (CanonicalIdError, TypeError) as exc:
                raise TaskPriorityQueueConflictError(
                    "queue report project_id must be canonical"
                ) from exc
            object.__setattr__(self, "project_id", project)
        if project.kind is not IdKind.PROJECT:
            raise TaskPriorityQueueConflictError(
                "queue report project_id must identify a project"
            )
        for field in ("newly_queued_count", "queued_count"):
            value = getattr(self, field)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise TaskPriorityQueueConflictError(
                    f"queue report {field} must be a non-negative integer"
                )
        if self.queued_count != len(self.items):
            raise TaskPriorityQueueConflictError(
                "queued_count does not match queue item count"
            )
        if self.newly_queued_count > self.queued_count:
            raise TaskPriorityQueueConflictError(
                "newly_queued_count exceeds queued_count"
            )


def _queue_sort_key(record: TaskRecord) -> tuple[int, str]:
    # T5.6 freezes the previously-unspecified numeric policy: larger priority
    # values run before smaller values; canonical task ID is the stable tie.
    return (-record.priority, record.task_id.value)


class TaskPriorityQueue:
    """Persistent-status deterministic priority queue for READY tasks.

    T5.6 does not introduce a second queue table. `QUEUED` is already part of
    the frozen task lifecycle and `priority` is immutable task data, so queue
    membership is derived from persisted task status and queue order is derived
    deterministically from `(priority DESC, task_id ASC)`.

    `enqueue_ready()` first runs T5.5 readiness resolution, then changes every
    currently READY task to QUEUED in one Task Registry transaction. It never
    moves QUEUED tasks to ASSIGNED; execution-slot/concurrency policy remains
    gated to later scheduler work.
    """

    def __init__(self, project_store: ProjectStore) -> None:
        if not isinstance(project_store, ProjectStore):
            raise TypeError("project_store must be ProjectStore")
        self._project_store = project_store
        self._tasks = TaskStore(project_store)
        self._dag = TaskDAGValidator(project_store)
        self._readiness = TaskReadinessResolver(project_store)

    @staticmethod
    def _dependencies_done(
        task: TaskRecord,
        records: dict[str, TaskRecord],
    ) -> bool:
        for dependency in task.dependencies:
            parent = records.get(dependency.value)
            if parent is None:
                raise TaskPriorityQueueConflictError(
                    "queue encountered missing dependency after DAG gate"
                )
            if parent.status is not TaskState.DONE:
                return False
        return True

    @staticmethod
    def _ordered_items(
        records: dict[str, TaskRecord],
    ) -> tuple[TaskQueueItem, ...]:
        queued = [
            record
            for record in records.values()
            if record.status is TaskState.QUEUED
        ]
        if len(queued) > MAX_PRIORITY_QUEUE_ITEMS:
            raise TaskPriorityQueueConflictError(
                "priority queue exceeds MAX_PRIORITY_QUEUE_ITEMS"
            )
        queued.sort(key=_queue_sort_key)
        return tuple(
            TaskQueueItem(
                task_id=record.task_id,
                priority=record.priority,
            )
            for record in queued
        )

    def ordered_queue(
        self,
        project_id: CanonicalId | str,
    ) -> tuple[TaskQueueItem, ...]:
        try:
            dag_report = self._dag.validate_project(project_id)
        except (TaskDAGConflictError, TaskDAGCycleError) as exc:
            raise TaskPriorityQueueConflictError(
                f"task graph is not queueable: {exc}"
            ) from exc

        handle = self._tasks._open_handle(dag_report.project_id)
        path = _registry_path(handle)
        if not path.exists():
            return ()
        _reject_registry_path(path)
        initialize_task_registry(path)

        with sqlite3.connect(path) as connection:
            verify_task_registry(connection)
            rows = connection.execute(
                """
                SELECT task_id, project_id, status, record_json
                FROM tasks
                ORDER BY task_id
                """
            ).fetchall()

        records: dict[str, TaskRecord] = {}
        for task_id, project_id_raw, status_raw, record_json in rows:
            record = _parse_record_json(record_json)
            if record.task_id.value != task_id:
                raise TaskPriorityQueueConflictError(
                    "task identity index mismatch during queue read"
                )
            if (
                record.project_id != handle.project_id
                or project_id_raw != handle.project_id.value
            ):
                raise TaskPriorityQueueConflictError(
                    "task project index mismatch during queue read"
                )
            if record.status.value != status_raw:
                raise TaskPriorityQueueConflictError(
                    "task status index mismatch during queue read"
                )
            records[task_id] = record

        dag_ids = tuple(sorted(item.value for item in dag_report.topological_order))
        if tuple(sorted(records)) != dag_ids:
            raise TaskPriorityQueueConflictError(
                "task graph changed during queue read"
            )

        for record in records.values():
            if record.status is TaskState.QUEUED:
                if record.attempt >= record.max_attempts:
                    raise TaskPriorityQueueConflictError(
                        "queued task has exhausted attempt budget: "
                        f"{record.task_id.value}"
                    )
                if not self._dependencies_done(
                    record,
                    records,
                ):
                    raise TaskPriorityQueueConflictError(
                        "queued task has unfinished dependency: "
                        f"{record.task_id.value}"
                    )

        return self._ordered_items(records)

    def peek_next(
        self,
        project_id: CanonicalId | str,
    ) -> TaskQueueItem | None:
        items = self.ordered_queue(project_id)
        return items[0] if items else None

    def enqueue_ready(
        self,
        project_id: CanonicalId | str,
    ) -> TaskPriorityQueueReport:
        try:
            self._readiness.resolve_project(project_id)
        except TaskReadinessConflictError as exc:
            raise TaskPriorityQueueConflictError(
                f"task project is not ready for queueing: {exc}"
            ) from exc

        # Revalidate DAG after readiness mutation so queueing never proceeds on
        # a graph that changed between readiness and this transaction.
        try:
            dag_report = self._dag.validate_project(project_id)
        except (TaskDAGConflictError, TaskDAGCycleError) as exc:
            raise TaskPriorityQueueConflictError(
                f"task graph is not queueable: {exc}"
            ) from exc

        handle = self._tasks._open_handle(dag_report.project_id)
        path = _registry_path(handle)
        if not path.exists():
            return TaskPriorityQueueReport(
                project_id=handle.project_id,
                newly_queued_count=0,
                queued_count=0,
                items=(),
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
                    SELECT task_id, project_id, status, record_json
                    FROM tasks
                    ORDER BY task_id
                    """
                ).fetchall()

                records: dict[str, TaskRecord] = {}
                raw_json: dict[str, str] = {}
                for task_id, project_id_raw, status_raw, record_json in rows:
                    record = _parse_record_json(record_json)
                    if record.task_id.value != task_id:
                        raise TaskPriorityQueueConflictError(
                            "task identity index mismatch during queue update"
                        )
                    if (
                        record.project_id != handle.project_id
                        or project_id_raw != handle.project_id.value
                    ):
                        raise TaskPriorityQueueConflictError(
                            "task project index mismatch during queue update"
                        )
                    if record.status.value != status_raw:
                        raise TaskPriorityQueueConflictError(
                            "task status index mismatch during queue update"
                        )
                    records[task_id] = record
                    raw_json[task_id] = record_json

                dag_ids = tuple(
                    sorted(item.value for item in dag_report.topological_order)
                )
                if tuple(sorted(records)) != dag_ids:
                    raise TaskPriorityQueueConflictError(
                        "task graph changed before queue update"
                    )

                ready_records: list[TaskRecord] = []
                for record in records.values():
                    if record.status is TaskState.READY:
                        if record.attempt >= record.max_attempts:
                            raise TaskPriorityQueueConflictError(
                                "READY task has exhausted attempt budget: "
                                f"{record.task_id.value}"
                            )
                        if not self._dependencies_done(record, records):
                            raise TaskPriorityQueueConflictError(
                                "READY task has unfinished dependency: "
                                f"{record.task_id.value}"
                            )
                        ready_records.append(record)
                    elif record.status is TaskState.QUEUED:
                        if record.attempt >= record.max_attempts:
                            raise TaskPriorityQueueConflictError(
                                "QUEUED task has exhausted attempt budget: "
                                f"{record.task_id.value}"
                            )
                        if not self._dependencies_done(record, records):
                            raise TaskPriorityQueueConflictError(
                                "QUEUED task has unfinished dependency: "
                                f"{record.task_id.value}"
                            )

                if len(ready_records) + sum(
                    record.status is TaskState.QUEUED
                    for record in records.values()
                ) > MAX_PRIORITY_QUEUE_ITEMS:
                    raise TaskPriorityQueueConflictError(
                        "priority queue exceeds MAX_PRIORITY_QUEUE_ITEMS"
                    )

                for current in sorted(ready_records, key=_queue_sort_key):
                    try:
                        target = require_task_transition(
                            current.status,
                            TaskState.QUEUED,
                        )
                    except (InvalidTaskStateTransition, TaskStateError) as exc:
                        raise TaskPriorityQueueConflictError(
                            "READY task cannot enter QUEUED state"
                        ) from exc

                    updated = replace(current, status=target)
                    updated_json = _canonical_record_json(updated)
                    connection.execute(
                        """
                        UPDATE tasks
                        SET status=?, record_json=?
                        WHERE task_id=? AND status=? AND record_json=?
                        """,
                        (
                            updated.status.value,
                            updated_json,
                            current.task_id.value,
                            current.status.value,
                            raw_json[current.task_id.value],
                        ),
                    )
                    if connection.execute(
                        "SELECT changes()"
                    ).fetchone()[0] != 1:
                        raise TaskPriorityQueueConflictError(
                            "task changed during queue transition"
                        )
                    records[current.task_id.value] = updated

                items = self._ordered_items(records)
                connection.commit()
                return TaskPriorityQueueReport(
                    project_id=handle.project_id,
                    newly_queued_count=len(ready_records),
                    queued_count=len(items),
                    items=items,
                )
            except Exception:
                connection.rollback()
                raise
        finally:
            connection.close()
