from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from ai.contracts.ids import IdKind
from ai.project.schema import (
    PROJECT_SCHEMA_CONTRACT_VERSION,
    PROJECT_SCHEMA_ID_KINDS,
    PROJECT_SCHEMA_TABLES,
    PROJECT_SCHEMA_VERSION,
    ProjectSchemaConflictError,
    ProjectSchemaVersionError,
    initialize_project_schema,
    verify_project_schema,
)


def test_schema_contract_version_is_explicit() -> None:
    assert PROJECT_SCHEMA_CONTRACT_VERSION == "1.0"
    assert PROJECT_SCHEMA_VERSION == 1


def test_schema_tables_are_minimal_and_exact() -> None:
    assert PROJECT_SCHEMA_TABLES == (
        "canonical_id_sequences",
        "project_schema_meta",
        "projects",
    )


def test_schema_id_kinds_follow_frozen_c1_1_contract() -> None:
    assert PROJECT_SCHEMA_ID_KINDS == tuple(kind.value for kind in IdKind)


def test_in_memory_bootstrap_creates_schema() -> None:
    with sqlite3.connect(":memory:") as connection:
        info = initialize_project_schema(connection)
        assert info.version == 1
        assert info.tables == PROJECT_SCHEMA_TABLES
        assert info.foreign_keys_enabled is True
        assert info.id_kinds == tuple(sorted(PROJECT_SCHEMA_ID_KINDS))


def test_bootstrap_is_idempotent_on_same_connection() -> None:
    with sqlite3.connect(":memory:") as connection:
        first = initialize_project_schema(connection)
        second = initialize_project_schema(connection)
        assert second == first


def test_restart_reopen_verifies_same_schema(tmp_path: Path) -> None:
    db = tmp_path / "project.sqlite3"
    with sqlite3.connect(db) as connection:
        initialize_project_schema(connection)
    with sqlite3.connect(db) as reopened:
        info = initialize_project_schema(reopened)
        assert info.version == PROJECT_SCHEMA_VERSION
        assert reopened.execute("PRAGMA foreign_keys").fetchone()[0] == 1


def test_user_version_matches_schema_meta() -> None:
    with sqlite3.connect(":memory:") as connection:
        initialize_project_schema(connection)
        meta = connection.execute(
            "SELECT schema_version FROM project_schema_meta WHERE singleton=1"
        ).fetchone()[0]
        user = connection.execute("PRAGMA user_version").fetchone()[0]
        assert meta == user == PROJECT_SCHEMA_VERSION


def test_sequence_rows_are_seeded_but_not_allocated() -> None:
    with sqlite3.connect(":memory:") as connection:
        initialize_project_schema(connection)
        rows = connection.execute(
            "SELECT kind,last_sequence FROM canonical_id_sequences ORDER BY kind"
        ).fetchall()
    assert [kind for kind, _ in rows] == sorted(PROJECT_SCHEMA_ID_KINDS)
    assert all(last == 0 for _, last in rows)


def test_valid_project_id_can_be_stored_by_future_s3_3() -> None:
    with sqlite3.connect(":memory:") as connection:
        initialize_project_schema(connection)
        connection.execute(
            "INSERT INTO projects(project_id) VALUES(?)",
            ("UAV-PROJ-000001",),
        )
        assert connection.execute("SELECT project_id FROM projects").fetchone()[0] == "UAV-PROJ-000001"


@pytest.mark.parametrize(
    "bad",
    [
        "UAV-PROJ-000000",
        "UAV-PROJ-00001",
        "UAV-PROJ-0000001",
        "uav-proj-000001",
        "PROJECT-000001",
        "UAV-PROJ-ABCDEF",
        " UAV-PROJ-000001",
    ],
)
def test_sqlite_rejects_invalid_project_ids(bad: str) -> None:
    with sqlite3.connect(":memory:") as connection:
        initialize_project_schema(connection)
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO projects(project_id) VALUES(?)",
                (bad,),
            )


def test_schema_rejects_nonempty_foreign_database() -> None:
    with sqlite3.connect(":memory:") as connection:
        connection.execute("CREATE TABLE unrelated(x INTEGER)")
        with pytest.raises(ProjectSchemaConflictError):
            initialize_project_schema(connection)


def test_schema_rejects_missing_expected_table() -> None:
    with sqlite3.connect(":memory:") as connection:
        initialize_project_schema(connection)
        connection.execute("DROP TABLE projects")
        with pytest.raises(ProjectSchemaConflictError):
            verify_project_schema(connection)


def test_schema_rejects_extra_table() -> None:
    with sqlite3.connect(":memory:") as connection:
        initialize_project_schema(connection)
        connection.execute("CREATE TABLE unexpected(x INTEGER)")
        with pytest.raises(ProjectSchemaConflictError):
            verify_project_schema(connection)


def test_schema_rejects_missing_id_sequence_row() -> None:
    with sqlite3.connect(":memory:") as connection:
        initialize_project_schema(connection)
        connection.execute(
            "DELETE FROM canonical_id_sequences WHERE kind=?",
            (IdKind.PROJECT.value,),
        )
        with pytest.raises(ProjectSchemaConflictError):
            verify_project_schema(connection)


def test_unknown_meta_version_fails_closed() -> None:
    with sqlite3.connect(":memory:") as connection:
        initialize_project_schema(connection)
        connection.execute("UPDATE project_schema_meta SET schema_version=99 WHERE singleton=1")
        connection.execute("PRAGMA user_version = 99")
        connection.commit()
        with pytest.raises(ProjectSchemaVersionError):
            verify_project_schema(connection)


def test_meta_and_user_version_mismatch_fails_closed() -> None:
    with sqlite3.connect(":memory:") as connection:
        initialize_project_schema(connection)
        connection.execute("PRAGMA user_version = 2")
        connection.commit()
        with pytest.raises(ProjectSchemaVersionError):
            verify_project_schema(connection)


def test_active_transaction_is_rejected() -> None:
    connection = sqlite3.connect(":memory:")
    try:
        connection.execute("BEGIN")
        with pytest.raises(ProjectSchemaConflictError, match="no active transaction"):
            initialize_project_schema(connection)
    finally:
        connection.rollback()
        connection.close()


def test_non_connection_is_rejected() -> None:
    with pytest.raises(TypeError):
        initialize_project_schema(object())  # type: ignore[arg-type]


def test_schema_module_does_not_choose_project_filesystem_layout() -> None:
    import ai.project.schema as module
    text = Path(module.__file__).read_text(encoding="utf-8")
    assert ".local/share" not in text
    assert "mkdir" not in text
    assert "project.json" not in text


def test_schema_module_does_not_expose_later_project_state_apis() -> None:
    import ai.project.schema as module
    for forbidden in (
        "RequirementSet",
        "create_project",
        "open_project",
        "allocate_id",
        "record_fact",
        "record_assumption",
        "record_decision",
        "transaction",
    ):
        assert not hasattr(module, forbidden)


def test_schema_module_has_no_network_ai_or_uav_runtime_imports() -> None:
    import ai.project.schema as module
    text = Path(module.__file__).read_text(encoding="utf-8").lower()
    for forbidden in (
        "requests",
        "httpx",
        "ollama",
        "mavsdk",
        "px4",
        "gazebo",
    ):
        assert forbidden not in text


def test_schema_module_has_no_retry_or_sleep_loop() -> None:
    import ai.project.schema as module
    text = Path(module.__file__).read_text(encoding="utf-8")
    assert "while True" not in text
    assert "time.sleep" not in text
    assert "asyncio.sleep" not in text
