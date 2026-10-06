from __future__ import annotations

from dataclasses import dataclass
import sqlite3

from ai.contracts.ids import CanonicalId, CanonicalIdError, IdKind
from ai.project.store import ProjectStore
from ai.tasks.store import (
    TaskNotFoundError,
    TaskStore,
    TaskStoreConflictError,
    _parse_id,
    _registry_path,
    _reject_registry_path,
    initialize_task_registry,
    verify_task_registry,
)


TASK_DEPENDENCY_CONTRACT_VERSION = "1.0"
MAX_TASK_DEPENDENCY_ISSUES = 4096
MAX_TASK_DIRECT_DEPENDENTS_RESULT = 4096


class TaskDependencyError(RuntimeError):
    """Base error for T5.3 task dependency operations."""


class TaskDependencyConflictError(TaskDependencyError):
    """Raised when dependency declarations are missing or inconsistent."""


@dataclass(frozen=True, order=True)
class MissingTaskDependency:
    child_task_id: CanonicalId
    dependency_task_id: CanonicalId

    def __post_init__(self) -> None:
        for field in ("child_task_id", "dependency_task_id"):
            value = getattr(self, field)
            if not isinstance(value, CanonicalId):
                try:
                    value = CanonicalId.parse(value)
                except (CanonicalIdError, TypeError) as exc:
                    raise TaskDependencyConflictError(
                        f"{field} must be a canonical task ID"
                    ) from exc
                object.__setattr__(self, field, value)
            if value.kind is not IdKind.TASK:
                raise TaskDependencyConflictError(
                    f"{field} must identify a task"
                )


class TaskDependencyStore:
    """Read/verify T5.3 persisted dependency declarations.

    T5.3 resolves only dependency identity/existence in the same project.
    It intentionally does not traverse the graph to detect cycles (T5.4),
    decide BLOCKED/READY (T5.5), or apply priority/scheduling policy.
    """

    def __init__(self, project_store: ProjectStore) -> None:
        if not isinstance(project_store, ProjectStore):
            raise TypeError("project_store must be ProjectStore")
        self._project_store = project_store
        self._tasks = TaskStore(project_store)

    def _open_registry(self, project_id: CanonicalId | str):
        handle = self._tasks._open_handle(project_id)
        path = _registry_path(handle)
        if not path.exists():
            raise TaskNotFoundError("task registry does not exist")
        _reject_registry_path(path)
        initialize_task_registry(path)
        return handle, path

    def dependencies(
        self,
        project_id: CanonicalId | str,
        task_id: CanonicalId | str,
    ) -> tuple[CanonicalId, ...]:
        handle, path = self._open_registry(project_id)
        task = _parse_id(task_id, kind=IdKind.TASK, field="task_id")

        with sqlite3.connect(path) as connection:
            verify_task_registry(connection)
            row = connection.execute(
                "SELECT 1 FROM tasks WHERE task_id=? AND project_id=?",
                (task.value, handle.project_id.value),
            ).fetchone()
            if row is None:
                raise TaskNotFoundError(f"task does not exist: {task.value}")
            rows = connection.execute(
                """
                SELECT dependency_task_id
                FROM task_dependency_edges
                WHERE child_task_id=?
                ORDER BY ordinal
                """,
                (task.value,),
            ).fetchall()

        return tuple(
            _parse_id(raw, kind=IdKind.TASK, field="dependency_task_id")
            for (raw,) in rows
        )

    def direct_dependents(
        self,
        project_id: CanonicalId | str,
        dependency_task_id: CanonicalId | str,
    ) -> tuple[CanonicalId, ...]:
        _handle, path = self._open_registry(project_id)
        dependency = _parse_id(
            dependency_task_id,
            kind=IdKind.TASK,
            field="dependency_task_id",
        )

        with sqlite3.connect(path) as connection:
            verify_task_registry(connection)
            rows = connection.execute(
                """
                SELECT child_task_id
                FROM task_dependency_edges
                WHERE dependency_task_id=?
                ORDER BY child_task_id
                LIMIT ?
                """,
                (
                    dependency.value,
                    MAX_TASK_DIRECT_DEPENDENTS_RESULT + 1,
                ),
            ).fetchall()

        if len(rows) > MAX_TASK_DIRECT_DEPENDENTS_RESULT:
            raise TaskDependencyConflictError(
                "direct dependents result exceeds bounded limit"
            )
        return tuple(
            _parse_id(raw, kind=IdKind.TASK, field="child_task_id")
            for (raw,) in rows
        )

    def missing_dependencies(
        self,
        project_id: CanonicalId | str,
    ) -> tuple[MissingTaskDependency, ...]:
        handle, path = self._open_registry(project_id)

        with sqlite3.connect(path) as connection:
            verify_task_registry(connection)
            rows = connection.execute(
                """
                SELECT e.child_task_id, e.dependency_task_id
                FROM task_dependency_edges e
                LEFT JOIN tasks d
                  ON d.task_id=e.dependency_task_id
                 AND d.project_id=?
                WHERE d.task_id IS NULL
                ORDER BY e.child_task_id, e.ordinal
                LIMIT ?
                """,
                (
                    handle.project_id.value,
                    MAX_TASK_DEPENDENCY_ISSUES + 1,
                ),
            ).fetchall()

        if len(rows) > MAX_TASK_DEPENDENCY_ISSUES:
            raise TaskDependencyConflictError(
                "missing dependency result exceeds bounded limit"
            )
        return tuple(
            MissingTaskDependency(
                child_task_id=child,
                dependency_task_id=dependency,
            )
            for child, dependency in rows
        )

    def verify_project(
        self,
        project_id: CanonicalId | str,
    ) -> None:
        missing = self.missing_dependencies(project_id)
        if not missing:
            return
        first = missing[0]
        raise TaskDependencyConflictError(
            "task dependency target does not exist in project: "
            f"{first.child_task_id.value} -> "
            f"{first.dependency_task_id.value}; "
            f"missing={len(missing)}"
        )
