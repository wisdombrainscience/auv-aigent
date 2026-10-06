from __future__ import annotations

from dataclasses import dataclass
import sqlite3

from ai.contracts.ids import CanonicalId, CanonicalIdError, IdKind
from ai.contracts.task_states import (
    InvalidTaskStateTransition,
    TaskState,
    TaskStateError,
    require_task_transition,
)
from ai.project.store import ProjectStore
from ai.tasks.dag import (
    TaskDAGConflictError,
    TaskDAGCycleError,
    TaskDAGValidator,
)
from ai.tasks.schema import TaskRecord
from ai.tasks.store import (
    TaskNotFoundError,
    TaskStore,
    TaskStoreConflictError,
    _canonical_record_json,
    _parse_record_json,
    _registry_path,
    _reject_registry_path,
    initialize_task_registry,
    verify_task_registry,
)


TASK_READINESS_CONTRACT_VERSION = "1.0"

READINESS_MANAGED_STATES = frozenset(
    {
        TaskState.CREATED,
        TaskState.BLOCKED,
        TaskState.READY,
    }
)


class TaskReadinessError(RuntimeError):
    """Base error for deterministic T5.5 readiness resolution."""


class TaskReadinessConflictError(TaskReadinessError):
    """Raised when readiness cannot be resolved safely."""


@dataclass(frozen=True)
class TaskReadinessDecision:
    task_id: CanonicalId
    previous_status: TaskState
    resolved_status: TaskState
    dependency_count: int
    done_dependency_count: int
    changed: bool

    def __post_init__(self) -> None:
        task = self.task_id
        if not isinstance(task, CanonicalId):
            try:
                task = CanonicalId.parse(task)
            except (CanonicalIdError, TypeError) as exc:
                raise TaskReadinessConflictError(
                    "decision task_id must be canonical"
                ) from exc
            object.__setattr__(self, "task_id", task)

        if task.kind is not IdKind.TASK:
            raise TaskReadinessConflictError(
                "decision task_id must identify a task"
            )
        if self.previous_status not in READINESS_MANAGED_STATES:
            raise TaskReadinessConflictError(
                "decision previous_status is outside readiness-managed states"
            )
        if self.resolved_status not in {
            TaskState.BLOCKED,
            TaskState.READY,
        }:
            raise TaskReadinessConflictError(
                "decision resolved_status must be BLOCKED or READY"
            )
        for field in ("dependency_count", "done_dependency_count"):
            value = getattr(self, field)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise TaskReadinessConflictError(
                    f"decision {field} must be a non-negative integer"
                )
        if self.done_dependency_count > self.dependency_count:
            raise TaskReadinessConflictError(
                "done dependency count exceeds dependency count"
            )
        if not isinstance(self.changed, bool):
            raise TaskReadinessConflictError(
                "decision changed must be boolean"
            )


@dataclass(frozen=True)
class TaskReadinessReport:
    project_id: CanonicalId
    task_count: int
    managed_count: int
    ready_count: int
    blocked_count: int
    changed_count: int
    decisions: tuple[TaskReadinessDecision, ...]

    def __post_init__(self) -> None:
        project = self.project_id
        if not isinstance(project, CanonicalId):
            try:
                project = CanonicalId.parse(project)
            except (CanonicalIdError, TypeError) as exc:
                raise TaskReadinessConflictError(
                    "report project_id must be canonical"
                ) from exc
            object.__setattr__(self, "project_id", project)

        if project.kind is not IdKind.PROJECT:
            raise TaskReadinessConflictError(
                "report project_id must identify a project"
            )

        for field in (
            "task_count",
            "managed_count",
            "ready_count",
            "blocked_count",
            "changed_count",
        ):
            value = getattr(self, field)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise TaskReadinessConflictError(
                    f"report {field} must be a non-negative integer"
                )

        if self.managed_count != len(self.decisions):
            raise TaskReadinessConflictError(
                "managed_count does not match decisions"
            )
        if self.ready_count + self.blocked_count != self.managed_count:
            raise TaskReadinessConflictError(
                "ready/blocked counts do not match managed_count"
            )
        if self.changed_count > self.managed_count:
            raise TaskReadinessConflictError(
                "changed_count exceeds managed_count"
            )


class TaskReadinessResolver:
    """Deterministically resolve CREATED/BLOCKED/READY from direct dependencies.

    Readiness rule:

      no dependencies                     -> READY
      every direct dependency is DONE     -> READY
      otherwise                           -> BLOCKED

    Only CREATED/BLOCKED/READY are mutated. Execution/review/recovery/human
    states are deliberately outside this package and remain untouched.

    Before any mutation, the complete project dependency graph must pass T5.3
    existence verification and T5.4 DAG validation. All readiness status
    changes for one project are then committed in one SQLite transaction.
    """

    def __init__(self, project_store: ProjectStore) -> None:
        if not isinstance(project_store, ProjectStore):
            raise TypeError("project_store must be ProjectStore")
        self._project_store = project_store
        self._tasks = TaskStore(project_store)
        self._dag = TaskDAGValidator(project_store)

    @staticmethod
    def _desired_status(
        task: TaskRecord,
        records: dict[str, TaskRecord],
    ) -> tuple[TaskState, int]:
        done_count = 0
        for dependency in task.dependencies:
            parent = records.get(dependency.value)
            if parent is None:
                raise TaskReadinessConflictError(
                    "readiness encountered a missing dependency after DAG gate"
                )
            if parent.status is TaskState.DONE:
                done_count += 1

        if done_count == len(task.dependencies):
            return TaskState.READY, done_count
        return TaskState.BLOCKED, done_count

    def resolve_project(
        self,
        project_id: CanonicalId | str,
    ) -> TaskReadinessReport:
        try:
            dag_report = self._dag.validate_project(project_id)
        except (TaskDAGConflictError, TaskDAGCycleError) as exc:
            raise TaskReadinessConflictError(
                f"task graph is not readiness-resolvable: {exc}"
            ) from exc

        handle = self._tasks._open_handle(dag_report.project_id)
        path = _registry_path(handle)

        if not path.exists():
            return TaskReadinessReport(
                project_id=dag_report.project_id,
                task_count=0,
                managed_count=0,
                ready_count=0,
                blocked_count=0,
                changed_count=0,
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
                        raise TaskReadinessConflictError(
                            "task identity index mismatch during readiness"
                        )
                    if (
                        record.project_id != handle.project_id
                        or project_id_raw != handle.project_id.value
                    ):
                        raise TaskReadinessConflictError(
                            "task project index mismatch during readiness"
                        )
                    if record.status.value != status_raw:
                        raise TaskReadinessConflictError(
                            "task status index mismatch during readiness"
                        )
                    records[task_id] = record
                    raw_json[task_id] = record_json

                current_ids = tuple(sorted(records))
                dag_ids = tuple(
                    sorted(item.value for item in dag_report.topological_order)
                )
                if current_ids != dag_ids:
                    raise TaskReadinessConflictError(
                        "task graph changed after DAG validation"
                    )

                decisions: list[TaskReadinessDecision] = []
                updates: list[tuple[TaskRecord, TaskRecord, str]] = []

                for task_id in (
                    item.value for item in dag_report.topological_order
                ):
                    current = records[task_id]
                    if current.status not in READINESS_MANAGED_STATES:
                        continue

                    desired, done_count = self._desired_status(
                        current,
                        records,
                    )
                    changed = current.status is not desired

                    if changed:
                        try:
                            require_task_transition(
                                current.status,
                                desired,
                            )
                        except (
                            InvalidTaskStateTransition,
                            TaskStateError,
                        ) as exc:
                            raise TaskReadinessConflictError(
                                "readiness requested an illegal task transition: "
                                f"{current.status.value} -> {desired.value}"
                            ) from exc

                        from dataclasses import replace
                        updated = replace(
                            current,
                            status=desired,
                        )
                        updates.append(
                            (
                                current,
                                updated,
                                raw_json[task_id],
                            )
                        )
                    decisions.append(
                        TaskReadinessDecision(
                            task_id=current.task_id,
                            previous_status=current.status,
                            resolved_status=desired,
                            dependency_count=len(current.dependencies),
                            done_dependency_count=done_count,
                            changed=changed,
                        )
                    )

                for current, updated, old_json in updates:
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
                            old_json,
                        ),
                    )
                    if connection.execute(
                        "SELECT changes()"
                    ).fetchone()[0] != 1:
                        raise TaskReadinessConflictError(
                            "task changed during readiness resolution"
                        )

                connection.commit()

                return TaskReadinessReport(
                    project_id=handle.project_id,
                    task_count=len(records),
                    managed_count=len(decisions),
                    ready_count=sum(
                        item.resolved_status is TaskState.READY
                        for item in decisions
                    ),
                    blocked_count=sum(
                        item.resolved_status is TaskState.BLOCKED
                        for item in decisions
                    ),
                    changed_count=sum(
                        item.changed
                        for item in decisions
                    ),
                    decisions=tuple(decisions),
                )
            except Exception:
                connection.rollback()
                raise
        finally:
            connection.close()
