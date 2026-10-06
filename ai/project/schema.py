from __future__ import annotations

from dataclasses import dataclass
import sqlite3
from types import MappingProxyType
from typing import Final

from ai.contracts.ids import IdKind


PROJECT_SCHEMA_CONTRACT_VERSION: Final[str] = "1.0"
PROJECT_SCHEMA_VERSION: Final[int] = 1


class ProjectSchemaError(RuntimeError):
    """Base error for the deterministic Phase-3 SQLite schema boundary."""


class ProjectSchemaVersionError(ProjectSchemaError):
    """Raised when an existing database has an unsupported schema version."""


class ProjectSchemaConflictError(ProjectSchemaError):
    """Raised when an existing database is partial or not a Remote UAV schema."""


@dataclass(frozen=True)
class ProjectSchemaInfo:
    version: int
    tables: tuple[str, ...]
    id_kinds: tuple[str, ...]
    foreign_keys_enabled: bool


_EXPECTED_TABLES: Final[tuple[str, ...]] = (
    "canonical_id_sequences",
    "project_schema_meta",
    "projects",
)
_EXPECTED_ID_KINDS: Final[tuple[str, ...]] = tuple(kind.value for kind in IdKind)

PROJECT_SCHEMA_TABLES = _EXPECTED_TABLES
PROJECT_SCHEMA_ID_KINDS = _EXPECTED_ID_KINDS

_ID_KIND_SQL = ",".join(repr(value) for value in _EXPECTED_ID_KINDS)

PROJECT_SCHEMA_DDL: Final[tuple[str, ...]] = (
    """
    CREATE TABLE project_schema_meta (
        singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
        schema_version INTEGER NOT NULL CHECK (schema_version >= 1)
    )
    """.strip(),
    """
    CREATE TABLE projects (
        project_id TEXT PRIMARY KEY NOT NULL,
        CHECK (length(project_id) = 15),
        CHECK (
            project_id GLOB
            'UAV-PROJ-[0-9][0-9][0-9][0-9][0-9][0-9]'
        ),
        CHECK (project_id <> 'UAV-PROJ-000000')
    )
    """.strip(),
    f"""
    CREATE TABLE canonical_id_sequences (
        kind TEXT PRIMARY KEY NOT NULL
            CHECK (kind IN ({_ID_KIND_SQL})),
        last_sequence INTEGER NOT NULL DEFAULT 0
            CHECK (last_sequence >= 0)
    )
    """.strip(),
)


def _require_connection(connection: sqlite3.Connection) -> sqlite3.Connection:
    if not isinstance(connection, sqlite3.Connection):
        raise TypeError("connection must be sqlite3.Connection")
    return connection


def _enable_foreign_keys(connection: sqlite3.Connection) -> None:
    # Must run outside a transaction. S3.1 does not introduce a general-purpose
    # transaction wrapper; that remains explicitly gated to S3.9.
    if connection.in_transaction:
        raise ProjectSchemaConflictError(
            "schema bootstrap/verification requires no active transaction"
        )
    connection.execute("PRAGMA foreign_keys = ON")
    enabled = connection.execute("PRAGMA foreign_keys").fetchone()[0]
    if enabled != 1:
        raise ProjectSchemaConflictError("SQLite foreign_keys could not be enabled")


def _user_tables(connection: sqlite3.Connection) -> tuple[str, ...]:
    rows = connection.execute(
        """
        SELECT name
        FROM sqlite_master
        WHERE type = 'table' AND name NOT LIKE 'sqlite_%'
        ORDER BY name
        """
    ).fetchall()
    return tuple(row[0] for row in rows)


def _read_schema_version(connection: sqlite3.Connection) -> int:
    row = connection.execute(
        "SELECT schema_version FROM project_schema_meta WHERE singleton = 1"
    ).fetchone()
    if row is None:
        raise ProjectSchemaConflictError("project_schema_meta singleton row is missing")
    if not isinstance(row[0], int) or isinstance(row[0], bool):
        raise ProjectSchemaConflictError("stored schema version is not an integer")
    return row[0]


def verify_project_schema(connection: sqlite3.Connection) -> ProjectSchemaInfo:
    """Verify an already-created S3.1 database without changing project data."""

    connection = _require_connection(connection)
    _enable_foreign_keys(connection)

    tables = _user_tables(connection)
    if tables != _EXPECTED_TABLES:
        raise ProjectSchemaConflictError(
            f"unexpected project schema tables: expected={_EXPECTED_TABLES!r} actual={tables!r}"
        )

    version = _read_schema_version(connection)
    user_version = connection.execute("PRAGMA user_version").fetchone()[0]
    if version != PROJECT_SCHEMA_VERSION or user_version != PROJECT_SCHEMA_VERSION:
        raise ProjectSchemaVersionError(
            "unsupported project schema version: "
            f"meta={version} user_version={user_version} expected={PROJECT_SCHEMA_VERSION}"
        )

    kinds = tuple(
        row[0]
        for row in connection.execute(
            "SELECT kind FROM canonical_id_sequences ORDER BY kind"
        ).fetchall()
    )
    expected_kinds = tuple(sorted(_EXPECTED_ID_KINDS))
    if kinds != expected_kinds:
        raise ProjectSchemaConflictError(
            f"canonical ID sequence rows are incomplete: expected={expected_kinds!r} actual={kinds!r}"
        )

    quick_check = connection.execute("PRAGMA quick_check").fetchone()
    if quick_check is None or quick_check[0] != "ok":
        raise ProjectSchemaConflictError("SQLite quick_check did not return ok")

    return ProjectSchemaInfo(
        version=version,
        tables=tables,
        id_kinds=kinds,
        foreign_keys_enabled=True,
    )


def initialize_project_schema(connection: sqlite3.Connection) -> ProjectSchemaInfo:
    """Create or verify the S3.1 SQLite schema on a caller-owned connection.

    S3.1 intentionally does not choose a filesystem path, create a project,
    allocate canonical IDs, write requirements/facts/assumptions/decisions, or
    expose a general transaction API. Those responsibilities remain gated to
    S3.2-S3.9.
    """

    connection = _require_connection(connection)
    _enable_foreign_keys(connection)

    existing = _user_tables(connection)
    if existing:
        return verify_project_schema(connection)

    try:
        connection.execute("BEGIN IMMEDIATE")
        for statement in PROJECT_SCHEMA_DDL:
            connection.execute(statement)
        connection.execute(
            "INSERT INTO project_schema_meta(singleton, schema_version) VALUES(1, ?)",
            (PROJECT_SCHEMA_VERSION,),
        )
        connection.executemany(
            "INSERT INTO canonical_id_sequences(kind, last_sequence) VALUES(?, 0)",
            ((kind,) for kind in _EXPECTED_ID_KINDS),
        )
        connection.execute(f"PRAGMA user_version = {PROJECT_SCHEMA_VERSION}")
        connection.commit()
    except Exception:
        if connection.in_transaction:
            connection.rollback()
        raise

    return verify_project_schema(connection)
