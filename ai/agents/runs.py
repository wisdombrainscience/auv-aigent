from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import re
import sqlite3
from typing import Any, Mapping

from ai.agents.mapping import TaskAgentMapper, TaskAgentMappingError
from ai.agents.output import SchemaEnforcedAgentOutput
from ai.contracts.agent_result import AgentResult, AgentResultError
from ai.contracts.ids import CanonicalId, CanonicalIdError, IdKind
from ai.project.schema import ProjectSchemaError, verify_project_schema
from ai.project.store import (
    ProjectHandle,
    ProjectNotFoundError,
    ProjectStore,
    ProjectStoreConflictError,
)
from ai.tasks.store import TaskNotFoundError, TaskStore, TaskStoreConflictError


AGENT_RUN_PERSISTENCE_CONTRACT_VERSION = "1.0"
AGENT_RUN_REGISTRY_SCHEMA_VERSION = 1
AGENT_RUN_REGISTRY_DB_FILENAME = "agent_run_registry.sqlite3"
MAX_AGENT_RUN_RECORD_JSON_BYTES = 9 * 1024 * 1024
MAX_AGENT_RUN_ROLE_CHARS = 128
MAX_AGENT_RUN_SCHEMA_ID_CHARS = 128
MAX_AGENT_RUN_IDENTITY_CHARS = 320

_EXPECTED_TABLES = (
    "agent_run_registry_meta",
    "agent_runs",
)

_RUN_RECORD_KEYS = frozenset(
    {
        "run_id",
        "project_id",
        "task_id",
        "role_id",
        "agent_identity",
        "schema_id",
        "result",
    }
)

_ROLE_RE = re.compile(r"^[a-z][a-z0-9_]{0,127}$")
_SCHEMA_ID_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,127}$")

_AGENT_RUN_DDL = (
    """
    CREATE TABLE agent_run_registry_meta (
        singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
        schema_version INTEGER NOT NULL CHECK (schema_version = 1)
    )
    """.strip(),
    """
    CREATE TABLE agent_runs (
        run_id TEXT PRIMARY KEY NOT NULL,
        project_id TEXT NOT NULL,
        task_id TEXT NOT NULL,
        role_id TEXT NOT NULL,
        schema_id TEXT NOT NULL,
        record_json TEXT NOT NULL,
        CHECK (
            run_id GLOB
            'AI-RUN-[0-9][0-9][0-9][0-9][0-9][0-9]'
        ),
        CHECK (run_id <> 'AI-RUN-000000'),
        CHECK (
            project_id GLOB
            'UAV-PROJ-[0-9][0-9][0-9][0-9][0-9][0-9]'
        ),
        CHECK (project_id <> 'UAV-PROJ-000000'),
        CHECK (
            task_id GLOB
            'TASK-[0-9][0-9][0-9][0-9][0-9][0-9]'
        ),
        CHECK (task_id <> 'TASK-000000'),
        CHECK (length(role_id) >= 1 AND length(role_id) <= 128),
        CHECK (length(schema_id) >= 1 AND length(schema_id) <= 128)
    )
    """.strip(),
)


class AgentRunPersistenceError(RuntimeError):
    """Base error for G6.6 persistent AI-RUN records."""


class AgentRunPersistenceConflictError(AgentRunPersistenceError):
    """Raised when run state is conflicting, corrupt, or unsafe to persist."""


class AgentRunNotFoundError(AgentRunPersistenceError):
    """Raised when one canonical AI-RUN record does not exist."""


def _parse_id(
    raw: CanonicalId | str,
    *,
    kind: IdKind,
    field: str,
) -> CanonicalId:
    try:
        value = raw if isinstance(raw, CanonicalId) else CanonicalId.parse(raw)
    except (CanonicalIdError, TypeError) as exc:
        raise AgentRunPersistenceConflictError(
            f"{field} must be a canonical {kind.value} ID"
        ) from exc
    if value.kind is not kind:
        raise AgentRunPersistenceConflictError(
            f"{field} must identify {kind.value}"
        )
    return value


def _clean_role_id(raw: Any) -> str:
    if not isinstance(raw, str) or _ROLE_RE.fullmatch(raw) is None:
        raise AgentRunPersistenceConflictError(
            "role_id must match [a-z][a-z0-9_]{0,127}"
        )
    return raw


def _clean_schema_id(raw: Any) -> str:
    if (
        not isinstance(raw, str)
        or len(raw) > MAX_AGENT_RUN_SCHEMA_ID_CHARS
        or _SCHEMA_ID_RE.fullmatch(raw) is None
    ):
        raise AgentRunPersistenceConflictError(
            "schema_id must match [A-Za-z][A-Za-z0-9_.-]{0,127}"
        )
    return raw


def _clean_agent_identity(raw: Any, *, role_id: str, task_id: CanonicalId) -> str:
    if not isinstance(raw, str):
        raise AgentRunPersistenceConflictError("agent_identity must be text")
    if not raw or raw != raw.strip() or any(ch.isspace() for ch in raw):
        raise AgentRunPersistenceConflictError(
            "agent_identity must be clean non-empty text without whitespace"
        )
    if len(raw) > MAX_AGENT_RUN_IDENTITY_CHARS:
        raise AgentRunPersistenceConflictError(
            "agent_identity exceeds MAX_AGENT_RUN_IDENTITY_CHARS"
        )
    expected = f"{role_id}:{task_id.value}"
    if raw != expected:
        raise AgentRunPersistenceConflictError(
            "agent_identity does not match immutable role/task identity"
        )
    return raw


@dataclass(frozen=True)
class AgentRunRecord:
    """Immutable persisted snapshot of one G6.5 schema-enforced proposal.

    G6.6 records identity and validated proposal output only. It does not add
    approval, review, context-manifest, artifact, provider, scheduler, or UAV
    authority.
    """

    run_id: CanonicalId
    project_id: CanonicalId
    task_id: CanonicalId
    role_id: str
    agent_identity: str
    schema_id: str
    result: AgentResult

    def __post_init__(self) -> None:
        run_id = _parse_id(self.run_id, kind=IdKind.AGENT_RUN, field="run_id")
        project_id = _parse_id(
            self.project_id, kind=IdKind.PROJECT, field="project_id"
        )
        task_id = _parse_id(self.task_id, kind=IdKind.TASK, field="task_id")
        role_id = _clean_role_id(self.role_id)
        schema_id = _clean_schema_id(self.schema_id)
        agent_identity = _clean_agent_identity(
            self.agent_identity,
            role_id=role_id,
            task_id=task_id,
        )
        if not isinstance(self.result, AgentResult):
            raise AgentRunPersistenceConflictError("result must be AgentResult")

        object.__setattr__(self, "run_id", run_id)
        object.__setattr__(self, "project_id", project_id)
        object.__setattr__(self, "task_id", task_id)
        object.__setattr__(self, "role_id", role_id)
        object.__setattr__(self, "agent_identity", agent_identity)
        object.__setattr__(self, "schema_id", schema_id)

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "AgentRunRecord":
        if not isinstance(raw, Mapping):
            raise AgentRunPersistenceConflictError(
                "agent run record must be an object"
            )
        keys = frozenset(raw.keys())
        if keys != _RUN_RECORD_KEYS:
            missing = sorted(_RUN_RECORD_KEYS - keys)
            extra = sorted(keys - _RUN_RECORD_KEYS)
            details: list[str] = []
            if missing:
                details.append("missing=" + ",".join(missing))
            if extra:
                details.append("extra=" + ",".join(str(item) for item in extra))
            raise AgentRunPersistenceConflictError(
                "agent run record must contain exactly the G6.6 fields"
                + (" (" + "; ".join(details) + ")" if details else "")
            )
        try:
            result = AgentResult.from_dict(raw["result"])
        except (AgentResultError, TypeError) as exc:
            raise AgentRunPersistenceConflictError(
                "stored result violates AgentResult contract"
            ) from exc
        return cls(
            run_id=raw["run_id"],
            project_id=raw["project_id"],
            task_id=raw["task_id"],
            role_id=raw["role_id"],
            agent_identity=raw["agent_identity"],
            schema_id=raw["schema_id"],
            result=result,
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "run_id": self.run_id.value,
            "project_id": self.project_id.value,
            "task_id": self.task_id.value,
            "role_id": self.role_id,
            "agent_identity": self.agent_identity,
            "schema_id": self.schema_id,
            "result": self.result.to_dict(),
        }


def _registry_path(handle: ProjectHandle) -> Path:
    return handle.layout.data_directories["ai_runs"] / AGENT_RUN_REGISTRY_DB_FILENAME


def _reject_registry_path(path: Path) -> None:
    if path.is_symlink():
        raise AgentRunPersistenceConflictError(
            "agent run registry database must not be a symbolic link"
        )
    if path.exists() and not path.is_file():
        raise AgentRunPersistenceConflictError(
            "agent run registry path is not a regular file"
        )
    if path.parent.is_symlink():
        raise AgentRunPersistenceConflictError(
            "ai_runs directory must not be a symbolic link"
        )


def _user_tables(
    connection: sqlite3.Connection,
    *,
    schema: str = "main",
) -> tuple[str, ...]:
    rows = connection.execute(
        f"""
        SELECT name
        FROM {schema}.sqlite_master
        WHERE type='table' AND name NOT LIKE 'sqlite_%'
        ORDER BY name
        """
    ).fetchall()
    return tuple(row[0] for row in rows)


def initialize_agent_run_registry(path: Path) -> None:
    path = Path(path)
    if not path.is_absolute():
        raise AgentRunPersistenceConflictError(
            "agent run registry path must be absolute"
        )
    _reject_registry_path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    connection = sqlite3.connect(path)
    try:
        existing = _user_tables(connection)
        if not existing:
            connection.execute("BEGIN IMMEDIATE")
            try:
                for statement in _AGENT_RUN_DDL:
                    connection.execute(statement)
                connection.execute(
                    "INSERT INTO agent_run_registry_meta(singleton, schema_version) "
                    "VALUES(1, ?)",
                    (AGENT_RUN_REGISTRY_SCHEMA_VERSION,),
                )
                connection.execute(
                    f"PRAGMA user_version={AGENT_RUN_REGISTRY_SCHEMA_VERSION}"
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        verify_agent_run_registry(connection)
    except sqlite3.DatabaseError as exc:
        raise AgentRunPersistenceConflictError(
            "agent run registry SQLite operation failed"
        ) from exc
    finally:
        connection.close()


def verify_agent_run_registry(connection: sqlite3.Connection) -> None:
    if not isinstance(connection, sqlite3.Connection):
        raise TypeError("connection must be sqlite3.Connection")
    tables = _user_tables(connection)
    if tables != _EXPECTED_TABLES:
        raise AgentRunPersistenceConflictError(
            "unexpected agent run registry tables: "
            f"expected={_EXPECTED_TABLES!r} actual={tables!r}"
        )
    row = connection.execute(
        "SELECT schema_version FROM agent_run_registry_meta WHERE singleton=1"
    ).fetchone()
    if row != (AGENT_RUN_REGISTRY_SCHEMA_VERSION,):
        raise AgentRunPersistenceConflictError(
            "agent run registry schema metadata is invalid"
        )
    user_version = connection.execute("PRAGMA user_version").fetchone()[0]
    if user_version != AGENT_RUN_REGISTRY_SCHEMA_VERSION:
        raise AgentRunPersistenceConflictError(
            "agent run registry user_version mismatch"
        )
    quick = connection.execute("PRAGMA quick_check").fetchone()
    if quick is None or quick[0] != "ok":
        raise AgentRunPersistenceConflictError(
            "agent run registry quick_check failed"
        )


def _canonical_record_json(record: AgentRunRecord) -> str:
    try:
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
    except (TypeError, ValueError) as exc:
        raise AgentRunPersistenceConflictError(
            "agent run record cannot be canonically encoded"
        ) from exc
    if len(raw.encode("utf-8")) > MAX_AGENT_RUN_RECORD_JSON_BYTES:
        raise AgentRunPersistenceConflictError(
            "agent run record exceeds MAX_AGENT_RUN_RECORD_JSON_BYTES"
        )
    return raw


def _parse_record_json(raw: str) -> AgentRunRecord:
    if not isinstance(raw, str):
        raise AgentRunPersistenceConflictError("record_json is not text")
    if len(raw.encode("utf-8")) > MAX_AGENT_RUN_RECORD_JSON_BYTES:
        raise AgentRunPersistenceConflictError(
            "record_json exceeds MAX_AGENT_RUN_RECORD_JSON_BYTES"
        )

    def reject_constant(value: str) -> None:
        raise AgentRunPersistenceConflictError(
            f"record_json contains non-finite JSON constant: {value}"
        )

    def object_without_duplicates(
        pairs: list[tuple[str, Any]],
    ) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise AgentRunPersistenceConflictError(
                    f"record_json contains duplicate JSON key: {key}"
                )
            result[key] = value
        return result

    try:
        payload = json.loads(
            raw,
            object_pairs_hook=object_without_duplicates,
            parse_constant=reject_constant,
        )
    except AgentRunPersistenceConflictError:
        raise
    except json.JSONDecodeError as exc:
        raise AgentRunPersistenceConflictError(
            "record_json is invalid JSON"
        ) from exc
    if not isinstance(payload, Mapping):
        raise AgentRunPersistenceConflictError(
            "record_json top level must be an object"
        )
    return AgentRunRecord.from_dict(payload)


def _attach_registry(connection: sqlite3.Connection, path: Path) -> None:
    _reject_registry_path(path)
    connection.execute("ATTACH DATABASE ? AS agent_run_registry", (str(path),))


def _verify_attached_registry(connection: sqlite3.Connection) -> None:
    tables = _user_tables(connection, schema="agent_run_registry")
    if tables != _EXPECTED_TABLES:
        raise AgentRunPersistenceConflictError(
            "attached agent run registry tables do not match G6.6"
        )
    row = connection.execute(
        "SELECT schema_version FROM "
        "agent_run_registry.agent_run_registry_meta WHERE singleton=1"
    ).fetchone()
    if row != (AGENT_RUN_REGISTRY_SCHEMA_VERSION,):
        raise AgentRunPersistenceConflictError(
            "attached agent run registry version mismatch"
        )


class AgentRunStore:
    """Persistent G6.6 store for immutable schema-enforced agent proposals.

    AI-RUN IDs are allocated globally from Project State and committed in the
    same SQLite transaction as the per-project run record. The store does not
    call a provider, advance task lifecycle, build a context manifest, create an
    artifact, review/validate/approve output, or command the UAV runtime.
    """

    def __init__(self, project_store: ProjectStore) -> None:
        if not isinstance(project_store, ProjectStore):
            raise TypeError("project_store must be ProjectStore")
        self._project_store = project_store
        self._tasks = TaskStore(project_store)
        self._mapper = TaskAgentMapper(project_store)

    def _open_handle(self, project_id: CanonicalId | str) -> ProjectHandle:
        project = _parse_id(
            project_id, kind=IdKind.PROJECT, field="project_id"
        )
        try:
            return self._project_store.open_project(project)
        except (ProjectNotFoundError, ProjectStoreConflictError) as exc:
            raise AgentRunPersistenceConflictError(
                f"project could not be opened: {exc}"
            ) from exc

    def _validate_output_against_current_task(
        self,
        output: SchemaEnforcedAgentOutput,
    ) -> ProjectHandle:
        if not isinstance(output, SchemaEnforcedAgentOutput):
            raise TypeError("output must be SchemaEnforcedAgentOutput")
        binding = output.binding
        handle = self._open_handle(binding.project_id)
        try:
            task = self._tasks.open_task(binding.project_id, binding.task_id)
            current_binding = self._mapper.map_task(
                binding.project_id, binding.task_id
            )
        except (
            TaskNotFoundError,
            TaskStoreConflictError,
            TaskAgentMappingError,
        ) as exc:
            raise AgentRunPersistenceConflictError(
                "persisted task/agent binding cannot be reopened safely"
            ) from exc

        if task.role != binding.role_id or current_binding != binding:
            raise AgentRunPersistenceConflictError(
                "task/agent binding changed before run persistence"
            )
        if task.output_schema != output.schema_id:
            raise AgentRunPersistenceConflictError(
                "task output schema changed before run persistence"
            )
        if output.result.to_dict().get("status") != "PROPOSED":
            raise AgentRunPersistenceConflictError(
                "only PROPOSED AgentResult output may be persisted by G6.6"
            )
        return handle

    def create_run(self, output: SchemaEnforcedAgentOutput) -> AgentRunRecord:
        handle = self._validate_output_against_current_task(output)
        registry_path = _registry_path(handle)
        initialize_agent_run_registry(registry_path)

        connection = sqlite3.connect(handle.database_path)
        try:
            verify_project_schema(connection)
            _attach_registry(connection, registry_path)
            _verify_attached_registry(connection)
            connection.execute("BEGIN IMMEDIATE")
            try:
                row = connection.execute(
                    "SELECT last_sequence FROM canonical_id_sequences WHERE kind=?",
                    (IdKind.AGENT_RUN.value,),
                ).fetchone()
                if (
                    row is None
                    or isinstance(row[0], bool)
                    or not isinstance(row[0], int)
                    or row[0] < 0
                ):
                    raise AgentRunPersistenceConflictError(
                        "agent_run canonical ID sequence is invalid"
                    )

                next_sequence = row[0] + 1
                try:
                    run_id = CanonicalId.from_sequence(
                        IdKind.AGENT_RUN, next_sequence
                    )
                except CanonicalIdError as exc:
                    raise AgentRunPersistenceConflictError(
                        "agent_run canonical ID sequence is exhausted"
                    ) from exc

                binding = output.binding
                record = AgentRunRecord(
                    run_id=run_id,
                    project_id=binding.project_id,
                    task_id=binding.task_id,
                    role_id=binding.role_id,
                    agent_identity=binding.agent.logical_identity,
                    schema_id=output.schema_id,
                    result=output.result,
                )
                record_json = _canonical_record_json(record)

                connection.execute(
                    """
                    INSERT INTO agent_run_registry.agent_runs(
                        run_id,
                        project_id,
                        task_id,
                        role_id,
                        schema_id,
                        record_json
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        record.run_id.value,
                        record.project_id.value,
                        record.task_id.value,
                        record.role_id,
                        record.schema_id,
                        record_json,
                    ),
                )
                connection.execute(
                    "UPDATE canonical_id_sequences SET last_sequence=? WHERE kind=?",
                    (next_sequence, IdKind.AGENT_RUN.value),
                )
                if connection.execute("SELECT changes()").fetchone()[0] != 1:
                    raise AgentRunPersistenceConflictError(
                        "agent_run canonical ID sequence update failed"
                    )
                connection.commit()
                return record
            except Exception:
                connection.rollback()
                raise
        except ProjectSchemaError as exc:
            raise AgentRunPersistenceConflictError(
                f"Project State schema is invalid: {exc}"
            ) from exc
        except sqlite3.DatabaseError as exc:
            raise AgentRunPersistenceConflictError(
                "agent run persistence SQLite operation failed"
            ) from exc
        finally:
            connection.close()

    def open_run(
        self,
        project_id: CanonicalId | str,
        run_id: CanonicalId | str,
    ) -> AgentRunRecord:
        handle = self._open_handle(project_id)
        run = _parse_id(run_id, kind=IdKind.AGENT_RUN, field="run_id")
        path = _registry_path(handle)
        if not path.exists():
            raise AgentRunNotFoundError("agent run registry does not exist")
        _reject_registry_path(path)

        connection = sqlite3.connect(path)
        try:
            verify_agent_run_registry(connection)
            row = connection.execute(
                """
                SELECT project_id, task_id, role_id, schema_id, record_json
                FROM agent_runs
                WHERE run_id=?
                """,
                (run.value,),
            ).fetchone()
            if row is None:
                raise AgentRunNotFoundError(
                    f"agent run does not exist: {run.value}"
                )
            record = _parse_record_json(row[4])
            if record.run_id != run:
                raise AgentRunPersistenceConflictError(
                    "stored agent run identity mismatch"
                )
            if record.project_id != handle.project_id or row[0] != handle.project_id.value:
                raise AgentRunPersistenceConflictError(
                    "stored agent run project mismatch"
                )
            if (
                row[1] != record.task_id.value
                or row[2] != record.role_id
                or row[3] != record.schema_id
            ):
                raise AgentRunPersistenceConflictError(
                    "agent run indexed fields do not match immutable record"
                )
        except sqlite3.DatabaseError as exc:
            raise AgentRunPersistenceConflictError(
                "agent run registry could not be read safely"
            ) from exc
        finally:
            connection.close()

        try:
            task = self._tasks.open_task(record.project_id, record.task_id)
            binding = self._mapper.map_task(record.project_id, record.task_id)
        except (
            TaskNotFoundError,
            TaskStoreConflictError,
            TaskAgentMappingError,
        ) as exc:
            raise AgentRunPersistenceConflictError(
                "stored agent run references invalid task/agent state"
            ) from exc
        if (
            task.role != record.role_id
            or task.output_schema != record.schema_id
            or binding.agent.logical_identity != record.agent_identity
        ):
            raise AgentRunPersistenceConflictError(
                "stored agent run no longer matches immutable task contract"
            )
        return record
