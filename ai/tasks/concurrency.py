from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, replace
import fcntl
import os
from pathlib import Path
import sqlite3
from typing import Iterator

from ai.contracts.ids import CanonicalId, CanonicalIdError, IdKind
from ai.contracts.task_states import (
    InvalidTaskStateTransition,
    TaskState,
    TaskStateError,
    require_task_transition,
)
from ai.project.schema import ProjectSchemaError, verify_project_schema
from ai.project.store import (
    ProjectNotFoundError,
    ProjectStore,
    ProjectStoreConflictError,
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


TASK_CONCURRENCY_CONTRACT_VERSION = "1.0"

# Frozen V8 resource baseline.
MAX_PARALLEL_AGENT_TASKS = 1

ACTIVE_AGENT_TASK_STATES = frozenset(
    {
        TaskState.ASSIGNED,
        TaskState.RUNNING,
    }
)

SCHEDULER_LOCK_FILENAME = ".task-concurrency.lock"
MAX_CONCURRENCY_SCAN_PROJECTS = 4096
MAX_CONCURRENCY_SCAN_TASKS = 65536


class TaskConcurrencyError(RuntimeError):
    """Base error for deterministic T5.11 execution-slot control."""


class TaskConcurrencyConflictError(TaskConcurrencyError):
    """Raised when global execution-slot state cannot be trusted."""


class TaskConcurrencyLimitError(TaskConcurrencyError):
    """Raised when the frozen single-agent execution slot is occupied."""


@dataclass(frozen=True, order=True)
class ActiveTaskSlot:
    project_id: CanonicalId
    task_id: CanonicalId
    status: TaskState
    priority: int

    def __post_init__(self) -> None:
        if self.project_id.kind is not IdKind.PROJECT:
            raise TaskConcurrencyConflictError(
                "active slot project_id must identify a project"
            )
        if self.task_id.kind is not IdKind.TASK:
            raise TaskConcurrencyConflictError(
                "active slot task_id must identify a task"
            )
        if self.status not in ACTIVE_AGENT_TASK_STATES:
            raise TaskConcurrencyConflictError(
                "active slot status is not concurrency-active"
            )


@dataclass(frozen=True)
class TaskConcurrencySnapshot:
    project_count: int
    active_count: int
    active_tasks: tuple[ActiveTaskSlot, ...]

    def __post_init__(self) -> None:
        if self.project_count < 0 or self.active_count < 0:
            raise TaskConcurrencyConflictError(
                "concurrency snapshot counts must be non-negative"
            )
        if self.active_count != len(self.active_tasks):
            raise TaskConcurrencyConflictError(
                "active_count does not match active_tasks"
            )
        if self.active_count > MAX_PARALLEL_AGENT_TASKS:
            raise TaskConcurrencyConflictError(
                "persisted active task count exceeds frozen concurrency limit"
            )

    @property
    def available_slots(self) -> int:
        return MAX_PARALLEL_AGENT_TASKS - self.active_count


@dataclass(frozen=True)
class TaskAssignment:
    project_id: CanonicalId
    task: TaskRecord

    def __post_init__(self) -> None:
        if self.project_id.kind is not IdKind.PROJECT:
            raise TaskConcurrencyConflictError(
                "assignment project_id must identify a project"
            )
        if self.task.project_id != self.project_id:
            raise TaskConcurrencyConflictError(
                "assignment task/project mismatch"
            )
        if self.task.status is not TaskState.ASSIGNED:
            raise TaskConcurrencyConflictError(
                "assignment task must be ASSIGNED"
            )


@dataclass(frozen=True)
class _QueuedCandidate:
    project_id: CanonicalId
    task: TaskRecord
    registry_path: Path

    @property
    def sort_key(self) -> tuple[int, str, str]:
        # Global deterministic order:
        #   1) higher numeric priority first
        #   2) project ID ascending
        #   3) task ID ascending
        return (
            -self.task.priority,
            self.project_id.value,
            self.task.task_id.value,
        )


def _parse_project_id(raw: CanonicalId | str) -> CanonicalId:
    try:
        value = raw if isinstance(raw, CanonicalId) else CanonicalId.parse(raw)
    except (CanonicalIdError, TypeError) as exc:
        raise TaskConcurrencyConflictError(
            "project_id must be canonical"
        ) from exc
    if value.kind is not IdKind.PROJECT:
        raise TaskConcurrencyConflictError(
            "project_id must identify a project"
        )
    return value


class TaskConcurrencyScheduler:
    """Global deterministic assignment gate for the frozen single AI slot.

    `ASSIGNED` and `RUNNING` consume the one global execution slot. States from
    RESULT_PROPOSED onward no longer consume the agent-execution slot.

    Source of truth remains persisted task status; no scheduler queue table or
    lease table is added. A process-wide filesystem lock under the Project
    State root serializes assignment decisions across local processes.

    T5.11 never starts an agent/model and never enters RUNNING. It only performs
    the QUEUED -> ASSIGNED transition after proving global capacity.
    """

    def __init__(self, project_store: ProjectStore) -> None:
        if not isinstance(project_store, ProjectStore):
            raise TypeError("project_store must be ProjectStore")
        self._project_store = project_store
        self._tasks = TaskStore(project_store)

    @property
    def lock_path(self) -> Path:
        return (
            self._project_store.projects_root.parent
            / SCHEDULER_LOCK_FILENAME
        )

    @contextmanager
    def _assignment_lock(self) -> Iterator[None]:
        path = self.lock_path
        path.parent.mkdir(parents=True, exist_ok=True)

        if path.is_symlink():
            raise TaskConcurrencyConflictError(
                "task concurrency lock must not be a symbolic link"
            )
        if path.exists() and not path.is_file():
            raise TaskConcurrencyConflictError(
                "task concurrency lock path must be a regular file"
            )

        flags = os.O_RDWR | os.O_CREAT
        fd = os.open(path, flags, 0o600)
        try:
            os.chmod(path, 0o600)
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            finally:
                os.close(fd)

    def _project_ids(self) -> tuple[CanonicalId, ...]:
        database = self._project_store.database_path
        if database.is_symlink():
            raise TaskConcurrencyConflictError(
                "Project State database must not be a symbolic link"
            )
        if not database.exists():
            return ()
        if not database.is_file():
            raise TaskConcurrencyConflictError(
                "Project State database must be a regular file"
            )

        connection = sqlite3.connect(database)
        try:
            try:
                verify_project_schema(connection)
            except ProjectSchemaError as exc:
                raise TaskConcurrencyConflictError(
                    f"Project State schema is invalid: {exc}"
                ) from exc

            rows = connection.execute(
                """
                SELECT project_id
                FROM projects
                ORDER BY project_id
                LIMIT ?
                """,
                (MAX_CONCURRENCY_SCAN_PROJECTS + 1,),
            ).fetchall()
        finally:
            connection.close()

        if len(rows) > MAX_CONCURRENCY_SCAN_PROJECTS:
            raise TaskConcurrencyConflictError(
                "project scan exceeds MAX_CONCURRENCY_SCAN_PROJECTS"
            )

        result: list[CanonicalId] = []
        for (raw,) in rows:
            result.append(_parse_project_id(raw))
        return tuple(result)

    def _registry_records(
        self,
        project_id: CanonicalId,
    ) -> tuple[Path | None, tuple[TaskRecord, ...]]:
        try:
            handle = self._project_store.open_project(project_id)
        except (ProjectNotFoundError, ProjectStoreConflictError) as exc:
            raise TaskConcurrencyConflictError(
                f"registered project could not be opened: {project_id.value}"
            ) from exc

        path = _registry_path(handle)
        if not path.exists():
            return None, ()

        _reject_registry_path(path)
        initialize_task_registry(path)

        connection = sqlite3.connect(path)
        try:
            verify_task_registry(connection)
            rows = connection.execute(
                """
                SELECT task_id, project_id, status, record_json
                FROM tasks
                ORDER BY task_id
                LIMIT ?
                """,
                (MAX_CONCURRENCY_SCAN_TASKS + 1,),
            ).fetchall()
        finally:
            connection.close()

        if len(rows) > MAX_CONCURRENCY_SCAN_TASKS:
            raise TaskConcurrencyConflictError(
                "task scan exceeds MAX_CONCURRENCY_SCAN_TASKS"
            )

        records: list[TaskRecord] = []
        for task_id, project_id_raw, status_raw, record_json in rows:
            record = _parse_record_json(record_json)
            if (
                record.task_id.value != task_id
                or record.project_id != project_id
                or project_id_raw != project_id.value
                or status_raw != record.status.value
            ):
                raise TaskConcurrencyConflictError(
                    "task registry identity/status mismatch during concurrency scan"
                )
            records.append(record)

        return path, tuple(records)

    @staticmethod
    def _dependencies_done(
        task: TaskRecord,
        records: dict[str, TaskRecord],
    ) -> bool:
        for dependency in task.dependencies:
            parent = records.get(dependency.value)
            if parent is None:
                raise TaskConcurrencyConflictError(
                    "queued task dependency is missing during assignment"
                )
            if parent.status is not TaskState.DONE:
                return False
        return True

    def _scan_locked(
        self,
        *,
        candidate_project: CanonicalId | None = None,
    ) -> tuple[
        TaskConcurrencySnapshot,
        tuple[_QueuedCandidate, ...],
    ]:
        project_ids = self._project_ids()

        active: list[ActiveTaskSlot] = []
        candidates: list[_QueuedCandidate] = []

        for project_id in project_ids:
            path, records_tuple = self._registry_records(project_id)
            if path is None:
                continue

            records = {
                record.task_id.value: record
                for record in records_tuple
            }

            for record in records_tuple:
                if record.status in ACTIVE_AGENT_TASK_STATES:
                    active.append(
                        ActiveTaskSlot(
                            project_id=project_id,
                            task_id=record.task_id,
                            status=record.status,
                            priority=record.priority,
                        )
                    )

                if record.status is not TaskState.QUEUED:
                    continue
                if (
                    candidate_project is not None
                    and project_id != candidate_project
                ):
                    continue
                if record.attempt >= record.max_attempts:
                    raise TaskConcurrencyConflictError(
                        "QUEUED task has exhausted attempt budget: "
                        f"{record.task_id.value}"
                    )
                if not self._dependencies_done(record, records):
                    raise TaskConcurrencyConflictError(
                        "QUEUED task has unfinished dependency: "
                        f"{record.task_id.value}"
                    )
                candidates.append(
                    _QueuedCandidate(
                        project_id=project_id,
                        task=record,
                        registry_path=path,
                    )
                )

        if len(active) > MAX_PARALLEL_AGENT_TASKS:
            raise TaskConcurrencyConflictError(
                "persisted active task count exceeds "
                "MAX_PARALLEL_AGENT_TASKS"
            )

        active.sort(
            key=lambda item: (
                item.project_id.value,
                item.task_id.value,
            )
        )
        candidates.sort(key=lambda item: item.sort_key)

        snapshot = TaskConcurrencySnapshot(
            project_count=len(project_ids),
            active_count=len(active),
            active_tasks=tuple(active),
        )
        return snapshot, tuple(candidates)

    def snapshot(self) -> TaskConcurrencySnapshot:
        with self._assignment_lock():
            snapshot, _candidates = self._scan_locked()
            return snapshot

    def _assign_locked(
        self,
        candidate: _QueuedCandidate,
    ) -> TaskAssignment:
        path = candidate.registry_path
        connection = sqlite3.connect(path)
        try:
            verify_task_registry(connection)
            connection.execute("BEGIN IMMEDIATE")
            try:
                row = connection.execute(
                    """
                    SELECT project_id, status, record_json,
                           execution_attempt, started_at_epoch_s,
                           deadline_at_epoch_s
                    FROM tasks
                    WHERE task_id=?
                    """,
                    (candidate.task.task_id.value,),
                ).fetchone()
                if row is None:
                    raise TaskNotFoundError(
                        f"task does not exist: {candidate.task.task_id.value}"
                    )

                current = _parse_record_json(row[2])
                if (
                    current.task_id != candidate.task.task_id
                    or current.project_id != candidate.project_id
                    or row[0] != candidate.project_id.value
                    or row[1] != current.status.value
                ):
                    raise TaskConcurrencyConflictError(
                        "assignment target registry identity/status mismatch"
                    )
                if current.status is not TaskState.QUEUED:
                    raise TaskConcurrencyConflictError(
                        "assignment target is no longer QUEUED"
                    )
                if current.attempt >= current.max_attempts:
                    raise TaskConcurrencyConflictError(
                        "assignment target exhausted attempt budget"
                    )
                if any(value is not None for value in row[3:]):
                    raise TaskConcurrencyConflictError(
                        "QUEUED assignment target has execution timing metadata"
                    )

                # Re-check dependency status inside the same project DB
                # transaction before assignment.
                dependency_records: dict[str, TaskRecord] = {}
                for dependency in current.dependencies:
                    dependency_row = connection.execute(
                        """
                        SELECT record_json
                        FROM tasks
                        WHERE task_id=?
                        """,
                        (dependency.value,),
                    ).fetchone()
                    if dependency_row is None:
                        raise TaskConcurrencyConflictError(
                            "assignment dependency disappeared"
                        )
                    dependency_records[dependency.value] = _parse_record_json(
                        dependency_row[0]
                    )
                if not self._dependencies_done(
                    current,
                    dependency_records,
                ):
                    raise TaskConcurrencyConflictError(
                        "assignment target has unfinished dependency"
                    )

                try:
                    target = require_task_transition(
                        current.status,
                        TaskState.ASSIGNED,
                    )
                except (
                    InvalidTaskStateTransition,
                    TaskStateError,
                ) as exc:
                    raise TaskConcurrencyConflictError(
                        "QUEUED task cannot enter ASSIGNED"
                    ) from exc

                updated = replace(
                    current,
                    status=target,
                )
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
                        row[2],
                    ),
                )
                if connection.execute(
                    "SELECT changes()"
                ).fetchone()[0] != 1:
                    raise TaskConcurrencyConflictError(
                        "task changed during assignment"
                    )

                connection.commit()
                return TaskAssignment(
                    project_id=candidate.project_id,
                    task=updated,
                )
            except Exception:
                connection.rollback()
                raise
        finally:
            connection.close()

    def assign_specific(
        self,
        project_id: CanonicalId | str,
        task_id: CanonicalId | str,
    ) -> TaskAssignment:
        project = _parse_project_id(project_id)
        try:
            task = (
                task_id
                if isinstance(task_id, CanonicalId)
                else CanonicalId.parse(task_id)
            )
        except (CanonicalIdError, TypeError) as exc:
            raise TaskConcurrencyConflictError(
                "task_id must be canonical"
            ) from exc
        if task.kind is not IdKind.TASK:
            raise TaskConcurrencyConflictError(
                "task_id must identify a task"
            )

        with self._assignment_lock():
            snapshot, candidates = self._scan_locked(
                candidate_project=project,
            )
            if snapshot.active_count >= MAX_PARALLEL_AGENT_TASKS:
                raise TaskConcurrencyLimitError(
                    "global execution slot is occupied"
                )

            candidate = next(
                (
                    item
                    for item in candidates
                    if item.task.task_id == task
                ),
                None,
            )
            if candidate is None:
                raise TaskConcurrencyConflictError(
                    "requested task is not a valid QUEUED assignment candidate"
                )

            return self._assign_locked(candidate)

    def assign_next(
        self,
        project_id: CanonicalId | str | None = None,
    ) -> TaskAssignment | None:
        project = (
            None
            if project_id is None
            else _parse_project_id(project_id)
        )

        with self._assignment_lock():
            snapshot, candidates = self._scan_locked(
                candidate_project=project,
            )
            if snapshot.active_count >= MAX_PARALLEL_AGENT_TASKS:
                return None
            if not candidates:
                return None
            return self._assign_locked(candidates[0])
