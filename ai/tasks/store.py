from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import sqlite3
from typing import Any, Mapping

from ai.artifacts.schema import ArtifactInputVersion
from ai.contracts.ids import CanonicalId, CanonicalIdError, IdKind
from ai.contracts.task_states import TaskState
from ai.project.schema import ProjectSchemaError, verify_project_schema
from ai.project.store import (
    ProjectHandle,
    ProjectNotFoundError,
    ProjectStore,
    ProjectStoreConflictError,
)
from ai.tasks.schema import (
    MAX_TASK_ATTEMPTS,
    TaskRecord,
    TaskRecordError,
)


TASK_STORE_CONTRACT_VERSION = "1.2"
TASK_REGISTRY_SCHEMA_VERSION = 3
TASK_DEPENDENCY_PERSISTENCE_CONTRACT_VERSION = "1.0"
TASK_REGISTRY_DB_FILENAME = "task_registry.sqlite3"
MAX_TASK_RECORD_JSON_BYTES = 512 * 1024

_T5_1_TABLES = (
    "task_registry_meta",
    "tasks",
)

_T5_3_TABLES = (
    "task_dependency_edges",
    "task_registry_meta",
    "tasks",
)

_EXPECTED_TABLES = _T5_3_TABLES

_TASK_DDL = (
    """
    CREATE TABLE task_registry_meta (
        singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
        schema_version INTEGER NOT NULL CHECK (schema_version = 3)
    )
    """.strip(),
    """
    CREATE TABLE tasks (
        task_id TEXT PRIMARY KEY NOT NULL,
        project_id TEXT NOT NULL,
        status TEXT NOT NULL,
        record_json TEXT NOT NULL,
        execution_attempt INTEGER,
        started_at_epoch_s REAL,
        deadline_at_epoch_s REAL,
        CHECK (
            task_id GLOB
            'TASK-[0-9][0-9][0-9][0-9][0-9][0-9]'
        ),
        CHECK (task_id <> 'TASK-000000'),
        CHECK (
            project_id GLOB
            'UAV-PROJ-[0-9][0-9][0-9][0-9][0-9][0-9]'
        ),
        CHECK (project_id <> 'UAV-PROJ-000000'),
        CHECK (
            (execution_attempt IS NULL
             AND started_at_epoch_s IS NULL
             AND deadline_at_epoch_s IS NULL)
            OR
            (execution_attempt IS NOT NULL
             AND execution_attempt >= 1
             AND execution_attempt <= 3
             AND started_at_epoch_s IS NOT NULL
             AND deadline_at_epoch_s IS NOT NULL
             AND deadline_at_epoch_s > started_at_epoch_s)
        )
    )
    """.strip(),
    """
    CREATE TABLE task_dependency_edges (
        child_task_id TEXT NOT NULL,
        ordinal INTEGER NOT NULL
            CHECK (ordinal >= 0 AND ordinal < 30),
        dependency_task_id TEXT NOT NULL,
        PRIMARY KEY (child_task_id, ordinal),
        UNIQUE (child_task_id, dependency_task_id),
        CHECK (
            child_task_id GLOB
            'TASK-[0-9][0-9][0-9][0-9][0-9][0-9]'
        ),
        CHECK (child_task_id <> 'TASK-000000'),
        CHECK (
            dependency_task_id GLOB
            'TASK-[0-9][0-9][0-9][0-9][0-9][0-9]'
        ),
        CHECK (dependency_task_id <> 'TASK-000000'),
        FOREIGN KEY (child_task_id)
            REFERENCES tasks(task_id)
            ON DELETE RESTRICT
    )
    """.strip(),
)


class TaskStoreError(RuntimeError):
    """Base error for T5.1 task persistence."""


class TaskStoreConflictError(TaskStoreError):
    """Raised when persisted task state is conflicting or corrupt."""


class TaskNotFoundError(TaskStoreError):
    """Raised when a requested task does not exist in one project."""


@dataclass(frozen=True)
class TaskCreateInput:
    title: str
    role: str
    priority: int
    dependencies: tuple[CanonicalId | str, ...]
    input_artifacts: tuple[ArtifactInputVersion, ...]
    output_schema: str
    validators: tuple[str, ...]
    review_required: bool
    max_attempts: int = MAX_TASK_ATTEMPTS

    def __post_init__(self) -> None:
        deps = self.dependencies
        if not isinstance(deps, tuple):
            try:
                deps = tuple(deps)
            except TypeError as exc:
                raise TaskStoreConflictError("dependencies must be iterable") from exc
            object.__setattr__(self, "dependencies", deps)

        artifacts = self.input_artifacts
        if not isinstance(artifacts, tuple):
            try:
                artifacts = tuple(artifacts)
            except TypeError as exc:
                raise TaskStoreConflictError("input_artifacts must be iterable") from exc
            object.__setattr__(self, "input_artifacts", artifacts)

        validators = self.validators
        if not isinstance(validators, tuple):
            try:
                validators = tuple(validators)
            except TypeError as exc:
                raise TaskStoreConflictError("validators must be iterable") from exc
            object.__setattr__(self, "validators", validators)

        # Reuse TaskRecord as the only field-level validator. T5.1 creation
        # always starts with attempt=0 and status=CREATED.
        try:
            probe = TaskRecord(
                task_id="TASK-000001",
                project_id="UAV-PROJ-000001",
                title=self.title,
                role=self.role,
                priority=self.priority,
                dependencies=deps,
                input_artifacts=artifacts,
                output_schema=self.output_schema,
                validators=validators,
                review_required=self.review_required,
                attempt=0,
                max_attempts=self.max_attempts,
                status=TaskState.CREATED,
            )
        except TaskRecordError as exc:
            raise TaskStoreConflictError(f"task create input is invalid: {exc}") from exc

        object.__setattr__(self, "title", probe.title)
        object.__setattr__(self, "role", probe.role)
        object.__setattr__(self, "priority", probe.priority)
        object.__setattr__(self, "dependencies", probe.dependencies)
        object.__setattr__(self, "input_artifacts", probe.input_artifacts)
        object.__setattr__(self, "output_schema", probe.output_schema)
        object.__setattr__(self, "validators", probe.validators)
        object.__setattr__(self, "review_required", probe.review_required)
        object.__setattr__(self, "max_attempts", probe.max_attempts)


def _parse_id(raw: CanonicalId | str, *, kind: IdKind, field: str) -> CanonicalId:
    try:
        value = raw if isinstance(raw, CanonicalId) else CanonicalId.parse(raw)
    except (CanonicalIdError, TypeError) as exc:
        raise TaskStoreConflictError(f"{field} must be canonical") from exc
    if value.kind is not kind:
        raise TaskStoreConflictError(f"{field} must identify {kind.value}")
    return value


def _registry_path(handle: ProjectHandle) -> Path:
    tasks_dir = handle.layout.data_directories["tasks"]
    return tasks_dir / TASK_REGISTRY_DB_FILENAME


def _reject_registry_path(path: Path) -> None:
    if path.is_symlink():
        raise TaskStoreConflictError("task registry database must not be a symbolic link")
    if path.exists() and not path.is_file():
        raise TaskStoreConflictError("task registry path is not a regular file")
    parent = path.parent
    if parent.is_symlink():
        raise TaskStoreConflictError("tasks directory must not be a symbolic link")


def _user_tables(connection: sqlite3.Connection, *, schema: str = "main") -> tuple[str, ...]:
    rows = connection.execute(
        f"""
        SELECT name
        FROM {schema}.sqlite_master
        WHERE type='table' AND name NOT LIKE 'sqlite_%'
        ORDER BY name
        """
    ).fetchall()
    return tuple(row[0] for row in rows)



def _insert_dependency_edges(
    connection: sqlite3.Connection,
    *,
    child_task_id: CanonicalId,
    dependencies: tuple[CanonicalId, ...],
    schema_prefix: str = "",
) -> None:
    prefix = f"{schema_prefix}." if schema_prefix else ""
    for ordinal, dependency in enumerate(dependencies):
        connection.execute(
            f"""
            INSERT INTO {prefix}task_dependency_edges(
                child_task_id,
                ordinal,
                dependency_task_id
            ) VALUES (?, ?, ?)
            """,
            (
                child_task_id.value,
                ordinal,
                dependency.value,
            ),
        )


def _backfill_dependency_edges(connection: sqlite3.Connection) -> None:
    rows = connection.execute(
        "SELECT task_id, record_json FROM tasks ORDER BY task_id"
    ).fetchall()
    for task_id, record_json in rows:
        record = _parse_record_json(record_json)
        if record.task_id.value != task_id:
            raise TaskStoreConflictError(
                "task identity mismatch during dependency migration"
            )
        _insert_dependency_edges(
            connection,
            child_task_id=record.task_id,
            dependencies=record.dependencies,
        )


def _verify_dependency_edge_record_consistency(
    connection: sqlite3.Connection,
) -> None:
    rows = connection.execute(
        "SELECT task_id, record_json FROM tasks ORDER BY task_id"
    ).fetchall()
    for task_id, record_json in rows:
        record = _parse_record_json(record_json)
        expected = tuple(
            (ordinal, dependency.value)
            for ordinal, dependency in enumerate(record.dependencies)
        )
        actual = tuple(
            connection.execute(
                """
                SELECT ordinal, dependency_task_id
                FROM task_dependency_edges
                WHERE child_task_id=?
                ORDER BY ordinal
                """,
                (task_id,),
            ).fetchall()
        )
        if actual != expected:
            raise TaskStoreConflictError(
                "task dependency edges do not match immutable task record"
            )

    extra = connection.execute(
        """
        SELECT COUNT(*)
        FROM task_dependency_edges e
        LEFT JOIN tasks t ON t.task_id=e.child_task_id
        WHERE t.task_id IS NULL
        """
    ).fetchone()[0]
    if extra:
        raise TaskStoreConflictError(
            "task dependency edges contain missing child tasks"
        )



def _verify_execution_timing_consistency(
    connection: sqlite3.Connection,
) -> None:
    import math

    rows = connection.execute(
        """
        SELECT task_id, status, record_json,
               execution_attempt, started_at_epoch_s, deadline_at_epoch_s
        FROM tasks
        ORDER BY task_id
        """
    ).fetchall()

    for (
        task_id, status_raw, record_json,
        execution_attempt, started_at, deadline_at,
    ) in rows:
        record = _parse_record_json(record_json)
        if record.task_id.value != task_id:
            raise TaskStoreConflictError(
                "task execution timing identity mismatch"
            )
        if record.status.value != status_raw:
            raise TaskStoreConflictError(
                "task registry status index mismatch"
            )

        timing = (execution_attempt, started_at, deadline_at)
        if record.status is TaskState.RUNNING:
            if any(value is None for value in timing):
                raise TaskStoreConflictError(
                    "RUNNING task is missing execution timeout metadata"
                )
            if (
                isinstance(execution_attempt, bool)
                or not isinstance(execution_attempt, int)
                or execution_attempt != record.attempt
            ):
                raise TaskStoreConflictError(
                    "RUNNING task execution attempt metadata mismatch"
                )
            if (
                isinstance(started_at, bool)
                or isinstance(deadline_at, bool)
                or not isinstance(started_at, (int, float))
                or not isinstance(deadline_at, (int, float))
                or not math.isfinite(float(started_at))
                or not math.isfinite(float(deadline_at))
                or float(started_at) < 0.0
                or float(deadline_at) <= float(started_at)
            ):
                raise TaskStoreConflictError(
                    "RUNNING task execution timing metadata is invalid"
                )
        elif any(value is not None for value in timing):
            raise TaskStoreConflictError(
                "non-RUNNING task retains execution timeout metadata"
            )


def initialize_task_registry(path: Path) -> None:
    path = Path(path)
    if not path.is_absolute():
        raise TaskStoreConflictError("task registry path must be absolute")
    _reject_registry_path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    connection = sqlite3.connect(path)
    try:
        existing = _user_tables(connection)
        if not existing:
            connection.execute("BEGIN IMMEDIATE")
            try:
                for statement in _TASK_DDL:
                    connection.execute(statement)
                connection.execute(
                    "INSERT INTO task_registry_meta(singleton, schema_version) VALUES(1, ?)",
                    (TASK_REGISTRY_SCHEMA_VERSION,),
                )
                connection.execute(
                    f"PRAGMA user_version={TASK_REGISTRY_SCHEMA_VERSION}"
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        elif existing == _T5_1_TABLES:
            row = connection.execute(
                "SELECT schema_version FROM task_registry_meta WHERE singleton=1"
            ).fetchone()
            user_version = connection.execute("PRAGMA user_version").fetchone()[0]
            if row != (1,) or user_version != 1:
                raise TaskStoreConflictError(
                    "T5.1/T5.2 registry metadata is not a valid migration source"
                )
            running = connection.execute(
                "SELECT COUNT(*) FROM tasks WHERE status=?",
                (TaskState.RUNNING.value,),
            ).fetchone()[0]
            if running:
                raise TaskStoreConflictError(
                    "cannot migrate RUNNING task without timeout start metadata"
                )

            connection.execute("BEGIN IMMEDIATE")
            try:
                connection.execute(
                    "ALTER TABLE task_registry_meta RENAME TO task_registry_meta_v1"
                )
                connection.execute(_TASK_DDL[0])
                connection.execute(
                    "INSERT INTO task_registry_meta(singleton, schema_version) VALUES(1, ?)",
                    (TASK_REGISTRY_SCHEMA_VERSION,),
                )
                connection.execute(
                    "ALTER TABLE tasks ADD COLUMN execution_attempt INTEGER"
                )
                connection.execute(
                    "ALTER TABLE tasks ADD COLUMN started_at_epoch_s REAL"
                )
                connection.execute(
                    "ALTER TABLE tasks ADD COLUMN deadline_at_epoch_s REAL"
                )
                connection.execute(_TASK_DDL[-1])
                _backfill_dependency_edges(connection)
                connection.execute("DROP TABLE task_registry_meta_v1")
                connection.execute(
                    f"PRAGMA user_version={TASK_REGISTRY_SCHEMA_VERSION}"
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        elif existing == _T5_3_TABLES:
            row = connection.execute(
                "SELECT schema_version FROM task_registry_meta WHERE singleton=1"
            ).fetchone()
            user_version = connection.execute("PRAGMA user_version").fetchone()[0]
            if row == (2,) and user_version == 2:
                running = connection.execute(
                    "SELECT COUNT(*) FROM tasks WHERE status=?",
                    (TaskState.RUNNING.value,),
                ).fetchone()[0]
                if running:
                    raise TaskStoreConflictError(
                        "cannot migrate RUNNING task without timeout start metadata"
                    )
                connection.execute("BEGIN IMMEDIATE")
                try:
                    connection.execute(
                        "ALTER TABLE task_registry_meta RENAME TO task_registry_meta_v2"
                    )
                    connection.execute(_TASK_DDL[0])
                    connection.execute(
                        "INSERT INTO task_registry_meta(singleton, schema_version) VALUES(1, ?)",
                        (TASK_REGISTRY_SCHEMA_VERSION,),
                    )
                    connection.execute(
                        "ALTER TABLE tasks ADD COLUMN execution_attempt INTEGER"
                    )
                    connection.execute(
                        "ALTER TABLE tasks ADD COLUMN started_at_epoch_s REAL"
                    )
                    connection.execute(
                        "ALTER TABLE tasks ADD COLUMN deadline_at_epoch_s REAL"
                    )
                    connection.execute("DROP TABLE task_registry_meta_v2")
                    connection.execute(
                        f"PRAGMA user_version={TASK_REGISTRY_SCHEMA_VERSION}"
                    )
                    connection.commit()
                except Exception:
                    connection.rollback()
                    raise
            elif row != (TASK_REGISTRY_SCHEMA_VERSION,) or user_version != TASK_REGISTRY_SCHEMA_VERSION:
                raise TaskStoreConflictError(
                    "task registry metadata is not a supported migration source"
                )
        else:
            raise TaskStoreConflictError(
                f"unexpected task registry tables: {existing!r}"
            )

        verify_task_registry(connection)
    finally:
        connection.close()
def verify_task_registry(connection: sqlite3.Connection) -> None:
    if not isinstance(connection, sqlite3.Connection):
        raise TypeError("connection must be sqlite3.Connection")
    tables = _user_tables(connection)
    if tables != _EXPECTED_TABLES:
        raise TaskStoreConflictError(
            f"unexpected task registry tables: expected={_EXPECTED_TABLES!r} actual={tables!r}"
        )
    row = connection.execute(
        "SELECT schema_version FROM task_registry_meta WHERE singleton=1"
    ).fetchone()
    if row != (TASK_REGISTRY_SCHEMA_VERSION,):
        raise TaskStoreConflictError("task registry schema metadata is invalid")
    user_version = connection.execute("PRAGMA user_version").fetchone()[0]
    if user_version != TASK_REGISTRY_SCHEMA_VERSION:
        raise TaskStoreConflictError("task registry user_version mismatch")
    quick = connection.execute("PRAGMA quick_check").fetchone()
    if quick is None or quick[0] != "ok":
        raise TaskStoreConflictError("task registry quick_check failed")

    _verify_dependency_edge_record_consistency(connection)
    _verify_execution_timing_consistency(connection)


def _canonical_record_json(record: TaskRecord) -> str:
    raw = (
        json.dumps(
            record.to_dict(),
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    )
    if len(raw.encode("utf-8")) > MAX_TASK_RECORD_JSON_BYTES:
        raise TaskStoreConflictError("task record exceeds MAX_TASK_RECORD_JSON_BYTES")
    return raw


def _parse_record_json(raw: str) -> TaskRecord:
    if not isinstance(raw, str):
        raise TaskStoreConflictError("task record_json is not text")
    if len(raw.encode("utf-8")) > MAX_TASK_RECORD_JSON_BYTES:
        raise TaskStoreConflictError("task record_json exceeds bounded size")

    def reject_constant(value: str) -> None:
        raise TaskStoreConflictError(f"task record contains non-finite JSON constant: {value}")

    def object_without_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise TaskStoreConflictError(f"task record contains duplicate JSON key: {key}")
            result[key] = value
        return result

    try:
        payload = json.loads(
            raw,
            object_pairs_hook=object_without_duplicates,
            parse_constant=reject_constant,
        )
    except TaskStoreConflictError:
        raise
    except json.JSONDecodeError as exc:
        raise TaskStoreConflictError("task record_json is invalid JSON") from exc
    if not isinstance(payload, Mapping):
        raise TaskStoreConflictError("task record_json top level must be an object")
    try:
        return TaskRecord.from_dict(payload)
    except TaskRecordError as exc:
        raise TaskStoreConflictError(f"stored task record violates T5.1: {exc}") from exc


def _attach_registry(connection: sqlite3.Connection, registry_path: Path) -> None:
    _reject_registry_path(registry_path)
    connection.execute("ATTACH DATABASE ? AS task_registry", (str(registry_path),))


def _verify_attached_registry(connection: sqlite3.Connection) -> None:
    tables = _user_tables(connection, schema="task_registry")
    if tables != _EXPECTED_TABLES:
        raise TaskStoreConflictError("attached task registry tables do not match T5.8")
    row = connection.execute(
        "SELECT schema_version FROM task_registry.task_registry_meta WHERE singleton=1"
    ).fetchone()
    if row != (TASK_REGISTRY_SCHEMA_VERSION,):
        raise TaskStoreConflictError("attached task registry version mismatch")


class TaskStore:
    """Persistent task registry with dependencies and T5.8 execution timing metadata.

    Creation allocates globally unique TASK IDs using the existing Phase-3
    canonical sequence, stores the exact bounded task contract, and forces
    attempt=0/status=CREATED.

    T5.3 materializes declared task dependencies as ordered edges. Dependency
    target existence is verified by TaskDependencyStore; cycle detection,
    READY resolution, queueing, retry, timeout, recovery, cancellation, and
    concurrency remain later Phase-5 packages.
    """

    def __init__(self, project_store: ProjectStore) -> None:
        if not isinstance(project_store, ProjectStore):
            raise TypeError("project_store must be ProjectStore")
        self._project_store = project_store

    def _open_handle(self, project_id: CanonicalId | str) -> ProjectHandle:
        project = _parse_id(project_id, kind=IdKind.PROJECT, field="project_id")
        try:
            return self._project_store.open_project(project)
        except (ProjectNotFoundError, ProjectStoreConflictError) as exc:
            raise TaskStoreConflictError(f"project could not be opened: {exc}") from exc

    def create_task(
        self,
        project_id: CanonicalId | str,
        value: TaskCreateInput,
    ) -> TaskRecord:
        if not isinstance(value, TaskCreateInput):
            raise TypeError("value must be TaskCreateInput")

        handle = self._open_handle(project_id)
        registry_path = _registry_path(handle)
        initialize_task_registry(registry_path)

        connection = sqlite3.connect(handle.database_path)
        try:
            verify_project_schema(connection)
            _attach_registry(connection, registry_path)
            _verify_attached_registry(connection)
            connection.execute("BEGIN IMMEDIATE")
            try:
                row = connection.execute(
                    "SELECT last_sequence FROM canonical_id_sequences WHERE kind=?",
                    (IdKind.TASK.value,),
                ).fetchone()
                if (
                    row is None
                    or isinstance(row[0], bool)
                    or not isinstance(row[0], int)
                    or row[0] < 0
                ):
                    raise TaskStoreConflictError("task canonical ID sequence is invalid")

                next_sequence = row[0] + 1
                try:
                    task_id = CanonicalId.from_sequence(IdKind.TASK, next_sequence)
                except CanonicalIdError as exc:
                    raise TaskStoreConflictError("task canonical ID sequence is exhausted") from exc

                record = TaskRecord(
                    task_id=task_id,
                    project_id=handle.project_id,
                    title=value.title,
                    role=value.role,
                    priority=value.priority,
                    dependencies=value.dependencies,
                    input_artifacts=value.input_artifacts,
                    output_schema=value.output_schema,
                    validators=value.validators,
                    review_required=value.review_required,
                    attempt=0,
                    max_attempts=value.max_attempts,
                    status=TaskState.CREATED,
                )
                record_json = _canonical_record_json(record)

                connection.execute(
                    """
                    INSERT INTO task_registry.tasks(
                        task_id,
                        project_id,
                        status,
                        record_json
                    ) VALUES (?, ?, ?, ?)
                    """,
                    (
                        record.task_id.value,
                        record.project_id.value,
                        record.status.value,
                        record_json,
                    ),
                )
                _insert_dependency_edges(
                    connection,
                    child_task_id=record.task_id,
                    dependencies=record.dependencies,
                    schema_prefix="task_registry",
                )
                connection.execute(
                    "UPDATE canonical_id_sequences SET last_sequence=? WHERE kind=?",
                    (next_sequence, IdKind.TASK.value),
                )
                if connection.execute("SELECT changes()").fetchone()[0] != 1:
                    raise TaskStoreConflictError("task canonical ID sequence update failed")
                connection.commit()
                return record
            except Exception:
                connection.rollback()
                raise
        except ProjectSchemaError as exc:
            raise TaskStoreConflictError(f"Project State schema is invalid: {exc}") from exc
        finally:
            connection.close()

    def open_task(
        self,
        project_id: CanonicalId | str,
        task_id: CanonicalId | str,
    ) -> TaskRecord:
        handle = self._open_handle(project_id)
        task = _parse_id(task_id, kind=IdKind.TASK, field="task_id")
        path = _registry_path(handle)
        if not path.exists():
            raise TaskNotFoundError("task registry does not exist")
        _reject_registry_path(path)

        connection = sqlite3.connect(path)
        try:
            verify_task_registry(connection)
            row = connection.execute(
                "SELECT project_id, status, record_json FROM tasks WHERE task_id=?",
                (task.value,),
            ).fetchone()
            if row is None:
                raise TaskNotFoundError(f"task does not exist: {task.value}")
            record = _parse_record_json(row[2])
            if record.task_id != task:
                raise TaskStoreConflictError("stored task identity mismatch")
            if record.project_id != handle.project_id or row[0] != handle.project_id.value:
                raise TaskStoreConflictError("stored task project mismatch")
            if row[1] != record.status.value:
                raise TaskStoreConflictError("task registry status index mismatch")
            return record
        finally:
            connection.close()

    def list_tasks(
        self,
        project_id: CanonicalId | str,
    ) -> tuple[TaskRecord, ...]:
        handle = self._open_handle(project_id)
        path = _registry_path(handle)
        if not path.exists():
            return ()
        _reject_registry_path(path)

        connection = sqlite3.connect(path)
        try:
            verify_task_registry(connection)
            rows = connection.execute(
                "SELECT task_id, project_id, status, record_json FROM tasks ORDER BY task_id"
            ).fetchall()
            records: list[TaskRecord] = []
            for task_id, project_id_raw, status, record_json in rows:
                record = _parse_record_json(record_json)
                if task_id != record.task_id.value:
                    raise TaskStoreConflictError("task registry identity index mismatch")
                if project_id_raw != record.project_id.value or record.project_id != handle.project_id:
                    raise TaskStoreConflictError("task registry project index mismatch")
                if status != record.status.value:
                    raise TaskStoreConflictError("task registry status index mismatch")
                records.append(record)
            return tuple(records)
        finally:
            connection.close()
