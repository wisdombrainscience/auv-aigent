from __future__ import annotations

import json
from pathlib import Path
import sqlite3

import pytest

import ai.project.store as store_module
from ai.contracts.ids import CanonicalId, IdKind
from ai.project.layout import PROJECT_DATA_DIRECTORIES
from ai.project.schema import initialize_project_schema
from ai.project.store import (
    MAX_PROJECT_METADATA_BYTES,
    PROJECT_METADATA_FORMAT_VERSION,
    PROJECT_STATE_DB_FILENAME,
    PROJECT_STORE_CONTRACT_VERSION,
    ProjectNotFoundError,
    ProjectStore,
    ProjectStoreConflictError,
)


def make_store(tmp_path: Path) -> ProjectStore:
    return ProjectStore(tmp_path / "remote-uav" / "projects")


def metadata(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def test_contract_constants_are_explicit() -> None:
    assert PROJECT_STORE_CONTRACT_VERSION == "1.0"
    assert PROJECT_METADATA_FORMAT_VERSION == 1
    assert PROJECT_STATE_DB_FILENAME == "project_state.sqlite3"
    assert MAX_PROJECT_METADATA_BYTES == 64 * 1024


def test_database_lives_beside_projects_directory(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    assert store.database_path == (
        tmp_path / "remote-uav" / "project_state.sqlite3"
    )


def test_create_first_project_allocates_project_one(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    handle = store.create_project()

    assert handle.project_id == CanonicalId.from_sequence(IdKind.PROJECT, 1)
    assert handle.project_id.value == "UAV-PROJ-000001"
    assert handle.database_path == store.database_path
    assert handle.layout.project_dir.is_dir()
    assert handle.layout.project_json.is_file()
    assert tuple(handle.layout.data_directories.keys()) == PROJECT_DATA_DIRECTORIES


def test_create_writes_exact_deterministic_project_metadata(tmp_path: Path) -> None:
    handle = make_store(tmp_path).create_project()
    payload = metadata(handle.layout.project_json)
    assert payload == {
        "format_version": 1,
        "project_id": "UAV-PROJ-000001",
        "project_schema_version": 1,
        "project_layout_contract_version": "1.0",
        "project_store_contract_version": "1.0",
    }
    assert dict(handle.metadata) == payload


def test_create_registers_project_and_advances_only_project_sequence(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    store.create_project()

    connection = sqlite3.connect(store.database_path)
    try:
        assert connection.execute(
            "SELECT project_id FROM projects"
        ).fetchall() == [("UAV-PROJ-000001",)]
        project_seq = connection.execute(
            "SELECT last_sequence FROM canonical_id_sequences WHERE kind='project'"
        ).fetchone()[0]
        assert project_seq == 1
        others = connection.execute(
            "SELECT kind,last_sequence FROM canonical_id_sequences WHERE kind <> 'project' ORDER BY kind"
        ).fetchall()
        assert all(sequence == 0 for _kind, sequence in others)
    finally:
        connection.close()


def test_second_create_allocates_monotonic_project_id(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    first = store.create_project()
    second = store.create_project()
    assert first.project_id.value == "UAV-PROJ-000001"
    assert second.project_id.value == "UAV-PROJ-000002"


def test_restart_open_reconstructs_same_project(tmp_path: Path) -> None:
    first_store = make_store(tmp_path)
    created = first_store.create_project()

    reopened = make_store(tmp_path).open_project(created.project_id.value)
    assert reopened.to_dict() == created.to_dict()


def test_restart_then_create_continues_sequence(tmp_path: Path) -> None:
    make_store(tmp_path).create_project()
    second = make_store(tmp_path).create_project()
    assert second.project_id.value == "UAV-PROJ-000002"


def test_open_does_not_create_missing_database(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    with pytest.raises(ProjectStoreConflictError, match="filesystem layout"):
        store.open_project("UAV-PROJ-000001")
    assert not store.database_path.exists()


def test_open_requires_project_kind(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    with pytest.raises(ProjectNotFoundError, match="not a project"):
        store.open_project("REQ-000001")


def test_open_rejects_invalid_canonical_id(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    with pytest.raises(ProjectNotFoundError, match="invalid"):
        store.open_project("UAV-PROJ-1")


def test_open_fails_if_db_row_is_missing(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    handle = store.create_project()
    connection = sqlite3.connect(store.database_path)
    try:
        connection.execute(
            "DELETE FROM projects WHERE project_id=?",
            (handle.project_id.value,),
        )
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(ProjectNotFoundError, match="not registered"):
        store.open_project(handle.project_id)


def test_open_fails_if_required_directory_is_missing(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    handle = store.create_project()
    (handle.layout.project_dir / "reports").rmdir()

    with pytest.raises(ProjectStoreConflictError, match="filesystem layout"):
        store.open_project(handle.project_id)


def test_open_fails_if_project_json_is_missing(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    handle = store.create_project()
    handle.layout.project_json.unlink()

    with pytest.raises(ProjectStoreConflictError, match="project.json is missing"):
        store.open_project(handle.project_id)


def test_open_rejects_project_json_symlink(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    handle = store.create_project()
    handle.layout.project_json.unlink()
    external = tmp_path / "external.json"
    external.write_text("{}", encoding="utf-8")
    handle.layout.project_json.symlink_to(external)

    with pytest.raises(ProjectStoreConflictError, match="symbolic link"):
        store.open_project(handle.project_id)


def test_open_rejects_duplicate_metadata_key(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    handle = store.create_project()
    handle.layout.project_json.write_text(
        '{"format_version":1,"project_id":"UAV-PROJ-000001",'
        '"project_id":"UAV-PROJ-000001","project_schema_version":1,'
        '"project_layout_contract_version":"1.0",'
        '"project_store_contract_version":"1.0"}',
        encoding="utf-8",
    )
    with pytest.raises(ProjectStoreConflictError, match="duplicate key"):
        store.open_project(handle.project_id)


def test_open_rejects_extra_metadata_key(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    handle = store.create_project()
    payload = metadata(handle.layout.project_json)
    payload["extra"] = True
    handle.layout.project_json.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ProjectStoreConflictError, match="keys do not match"):
        store.open_project(handle.project_id)


@pytest.mark.parametrize(
    "field,value,match",
    [
        ("format_version", 2, "format version"),
        ("project_id", "UAV-PROJ-000002", "ID mismatch"),
        ("project_schema_version", 2, "schema version"),
        ("project_layout_contract_version", "9.9", "layout version"),
        ("project_store_contract_version", "9.9", "store version"),
    ],
)
def test_open_rejects_metadata_contract_mismatch(
    tmp_path: Path,
    field: str,
    value: object,
    match: str,
) -> None:
    store = make_store(tmp_path)
    handle = store.create_project()
    payload = metadata(handle.layout.project_json)
    payload[field] = value
    handle.layout.project_json.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ProjectStoreConflictError, match=match):
        store.open_project(handle.project_id)


def test_open_rejects_non_object_metadata(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    handle = store.create_project()
    handle.layout.project_json.write_text("[]", encoding="utf-8")
    with pytest.raises(ProjectStoreConflictError, match="top level"):
        store.open_project(handle.project_id)


def test_open_rejects_nonfinite_json_constant(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    handle = store.create_project()
    handle.layout.project_json.write_text(
        '{"format_version":NaN}', encoding="utf-8"
    )
    with pytest.raises(ProjectStoreConflictError, match="non-finite"):
        store.open_project(handle.project_id)


def test_open_rejects_oversized_metadata(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    handle = store.create_project()
    handle.layout.project_json.write_bytes(b"x" * (MAX_PROJECT_METADATA_BYTES + 1))
    with pytest.raises(ProjectStoreConflictError, match="too large"):
        store.open_project(handle.project_id)


def test_database_symlink_is_rejected(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    store.database_path.parent.mkdir(parents=True)
    external = tmp_path / "outside.sqlite3"
    sqlite3.connect(external).close()
    store.database_path.symlink_to(external)

    with pytest.raises(ProjectStoreConflictError, match="symbolic link"):
        store.create_project()


def test_orphan_prospective_directory_fails_without_advancing_sequence(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    store._prepare_storage_parent()
    connection = sqlite3.connect(store.database_path)
    try:
        initialize_project_schema(connection)
    finally:
        connection.close()

    orphan = store.projects_root / "UAV-PROJ-000001"
    orphan.mkdir(parents=True)

    with pytest.raises(ProjectStoreConflictError, match="already exists"):
        store.create_project()

    connection = sqlite3.connect(store.database_path)
    try:
        assert connection.execute(
            "SELECT last_sequence FROM canonical_id_sequences WHERE kind='project'"
        ).fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM projects").fetchone()[0] == 0
    finally:
        connection.close()


def test_metadata_write_failure_rolls_back_db_and_removes_new_project_dir(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = make_store(tmp_path)

    def fail_write(path, metadata):
        raise OSError("injected metadata failure")

    monkeypatch.setattr(store_module, "_write_project_metadata", fail_write)

    with pytest.raises(OSError, match="injected"):
        store.create_project()

    assert not (store.projects_root / "UAV-PROJ-000001").exists()
    connection = sqlite3.connect(store.database_path)
    try:
        assert connection.execute("SELECT COUNT(*) FROM projects").fetchone()[0] == 0
        assert connection.execute(
            "SELECT last_sequence FROM canonical_id_sequences WHERE kind='project'"
        ).fetchone()[0] == 0
    finally:
        connection.close()


def test_open_is_read_only_for_project_metadata(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    handle = store.create_project()
    before = handle.layout.project_json.read_bytes()
    store.open_project(handle.project_id)
    after = handle.layout.project_json.read_bytes()
    assert after == before


def test_handle_metadata_mapping_is_immutable(tmp_path: Path) -> None:
    handle = make_store(tmp_path).create_project()
    with pytest.raises(TypeError):
        handle.metadata["project_id"] = "UAV-PROJ-999999"  # type: ignore[index]


def test_s3_3_does_not_create_requirement_or_artifact_state(tmp_path: Path) -> None:
    handle = make_store(tmp_path).create_project()
    for directory in ("requirements", "tasks", "ai_runs", "evidence"):
        assert list((handle.layout.project_dir / directory).iterdir()) == []


def test_s3_3_store_exposes_no_general_transaction_api(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    for name in ("transaction", "begin", "commit", "rollback"):
        assert not hasattr(store, name)
