from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sqlite3

import pytest

import ai.project.requirement_versions as version_module
from ai.project.requirements import RequirementSetStatus
from ai.project.requirement_versions import (
    MAX_REQUIREMENT_CURRENT_BYTES,
    MAX_REQUIREMENT_SNAPSHOT_BYTES,
    MAX_REQUIREMENT_VERSION,
    REQUIREMENT_CURRENT_FILENAME,
    REQUIREMENT_VERSION_FORMAT_VERSION,
    REQUIREMENT_VERSION_STORE_CONTRACT_VERSION,
    RequirementVersionConflictError,
    RequirementVersionInput,
    RequirementVersionNotFoundError,
    RequirementVersionStore,
)
from ai.project.store import ProjectStore


def make_store(tmp_path: Path) -> tuple[ProjectStore, object]:
    project_store = ProjectStore(
        tmp_path / "remote-uav" / "projects"
    )
    handle = project_store.create_project()
    return project_store, handle


def make_version_store(tmp_path: Path):
    project_store, handle = make_store(tmp_path)
    return RequirementVersionStore(project_store), project_store, handle


def test_constants_are_explicit_and_bounded() -> None:
    assert REQUIREMENT_VERSION_STORE_CONTRACT_VERSION == "1.0"
    assert REQUIREMENT_VERSION_FORMAT_VERSION == 1
    assert REQUIREMENT_CURRENT_FILENAME == "current.json"
    assert MAX_REQUIREMENT_VERSION == 999_999
    assert MAX_REQUIREMENT_SNAPSHOT_BYTES == 8 * 1024 * 1024
    assert MAX_REQUIREMENT_CURRENT_BYTES == 16 * 1024


def test_initial_version_allocates_canonical_requirement_ids(tmp_path: Path) -> None:
    store, _projects, handle = make_version_store(tmp_path)

    version = store.create_initial(
        handle.project_id,
        [
            "Payload shall not exceed 2 kg.",
            "Minimum endurance shall be 20 minutes.",
        ],
    )

    assert version.version == 1
    assert version.parent_version is None
    assert version.requirement_set.status is RequirementSetStatus.DRAFT
    assert [
        item.requirement_id.value
        for item in version.requirement_set.requirements
    ] == ["REQ-000001", "REQ-000002"]


def test_requirement_sequence_is_persistent_and_global_across_projects(
    tmp_path: Path,
) -> None:
    project_store = ProjectStore(
        tmp_path / "remote-uav" / "projects"
    )
    first = project_store.create_project()
    second = project_store.create_project()
    store = RequirementVersionStore(project_store)

    v1 = store.create_initial(first.project_id, ["A"])
    v2 = store.create_initial(second.project_id, ["B"])

    assert v1.requirement_set.requirements[0].requirement_id.value == "REQ-000001"
    assert v2.requirement_set.requirements[0].requirement_id.value == "REQ-000002"


def test_append_reuses_existing_id_and_allocates_new_id(tmp_path: Path) -> None:
    store, _projects, handle = make_version_store(tmp_path)
    first = store.create_initial(handle.project_id, ["A", "B"])
    first_id = first.requirement_set.requirements[0].requirement_id

    second = store.append_version(
        handle.project_id,
        [
            RequirementVersionInput(
                requirement_id=first_id,
                text="A changed",
            ),
            RequirementVersionInput(text="C"),
        ],
    )

    assert second.version == 2
    assert second.parent_version == 1
    assert [
        (item.requirement_id.value, item.text)
        for item in second.requirement_set.requirements
    ] == [
        ("REQ-000001", "A changed"),
        ("REQ-000003", "C"),
    ]


def test_old_version_remains_readable_after_append(tmp_path: Path) -> None:
    store, _projects, handle = make_version_store(tmp_path)
    first = store.create_initial(handle.project_id, ["A"])
    store.append_version(
        handle.project_id,
        [
            RequirementVersionInput(
                requirement_id=first.requirement_set.requirements[0].requirement_id,
                text="A changed",
            )
        ],
    )

    reopened_first = store.open_version(handle.project_id, 1)
    reopened_second = store.open_version(handle.project_id, 2)

    assert reopened_first.requirement_set.requirements[0].text == "A"
    assert reopened_second.requirement_set.requirements[0].text == "A changed"


def test_restart_open_latest_returns_same_version(tmp_path: Path) -> None:
    store, projects, handle = make_version_store(tmp_path)
    store.create_initial(handle.project_id, ["A"])
    second = store.append_version(handle.project_id, ["B"])

    restarted = RequirementVersionStore(
        ProjectStore(projects.projects_root)
    )
    assert restarted.open_latest(handle.project_id) == second


def test_snapshot_and_hash_files_are_immutable_named_versions(tmp_path: Path) -> None:
    store, _projects, handle = make_version_store(tmp_path)
    store.create_initial(handle.project_id, ["A"])

    directory = handle.layout.data_directories["requirements"]
    assert (directory / "v000001.json").is_file()
    assert (directory / "v000001.json.sha256").is_file()
    assert (directory / "current.json").is_file()


def test_current_pointer_tracks_latest_hash(tmp_path: Path) -> None:
    store, _projects, handle = make_version_store(tmp_path)
    store.create_initial(handle.project_id, ["A"])

    directory = handle.layout.data_directories["requirements"]
    snapshot = (directory / "v000001.json").read_bytes()
    current = json.loads(
        (directory / "current.json").read_text(encoding="utf-8")
    )
    assert current["latest_version"] == 1
    assert current["snapshot_sha256"] == hashlib.sha256(snapshot).hexdigest()


def test_initial_version_rejects_caller_supplied_requirement_id(
    tmp_path: Path,
) -> None:
    store, _projects, handle = make_version_store(tmp_path)
    with pytest.raises(
        RequirementVersionConflictError,
        match="allocate all requirement IDs",
    ):
        store.create_initial(
            handle.project_id,
            [
                RequirementVersionInput(
                    requirement_id="REQ-000001",
                    text="A",
                )
            ],
        )


def test_append_rejects_id_not_present_in_previous_version(tmp_path: Path) -> None:
    store, _projects, handle = make_version_store(tmp_path)
    store.create_initial(handle.project_id, ["A"])

    with pytest.raises(
        RequirementVersionConflictError,
        match="not present in the previous version",
    ):
        store.append_version(
            handle.project_id,
            [
                RequirementVersionInput(
                    requirement_id="REQ-000999",
                    text="Injected",
                )
            ],
        )


def test_append_rejects_duplicate_existing_id(tmp_path: Path) -> None:
    store, _projects, handle = make_version_store(tmp_path)
    first = store.create_initial(handle.project_id, ["A"])
    rid = first.requirement_set.requirements[0].requirement_id

    with pytest.raises(
        RequirementVersionConflictError,
        match="duplicate requirement ID",
    ):
        store.append_version(
            handle.project_id,
            [
                RequirementVersionInput(requirement_id=rid, text="A1"),
                RequirementVersionInput(requirement_id=rid, text="A2"),
            ],
        )


def test_append_without_previous_version_fails(tmp_path: Path) -> None:
    store, _projects, handle = make_version_store(tmp_path)
    with pytest.raises(
        RequirementVersionNotFoundError,
        match="no previous",
    ):
        store.append_version(handle.project_id, ["A"])


def test_second_initial_version_fails_without_overwriting(tmp_path: Path) -> None:
    store, _projects, handle = make_version_store(tmp_path)
    first = store.create_initial(handle.project_id, ["A"])

    with pytest.raises(RequirementVersionConflictError):
        store.create_initial(handle.project_id, ["B"])

    assert store.open_latest(handle.project_id) == first


def test_empty_requirement_version_is_allowed(tmp_path: Path) -> None:
    store, _projects, handle = make_version_store(tmp_path)
    version = store.create_initial(handle.project_id, [])
    assert version.requirement_set.requirements == ()


def test_approved_status_is_preserved(tmp_path: Path) -> None:
    store, _projects, handle = make_version_store(tmp_path)
    version = store.create_initial(
        handle.project_id,
        ["A"],
        status=RequirementSetStatus.APPROVED,
    )
    assert version.requirement_set.status is RequirementSetStatus.APPROVED
    assert store.open_latest(handle.project_id).requirement_set.status is RequirementSetStatus.APPROVED


def test_requirement_sequence_advances_only_for_new_ids(tmp_path: Path) -> None:
    store, projects, handle = make_version_store(tmp_path)
    first = store.create_initial(handle.project_id, ["A", "B"])
    reused = first.requirement_set.requirements[0].requirement_id

    store.append_version(
        handle.project_id,
        [
            RequirementVersionInput(requirement_id=reused, text="A2"),
            RequirementVersionInput(text="C"),
        ],
    )

    with sqlite3.connect(projects.database_path) as connection:
        seq = connection.execute(
            "SELECT last_sequence FROM canonical_id_sequences WHERE kind='requirement'"
        ).fetchone()[0]
    assert seq == 3


def test_tampered_snapshot_is_detected_by_hash(tmp_path: Path) -> None:
    store, _projects, handle = make_version_store(tmp_path)
    store.create_initial(handle.project_id, ["A"])

    path = handle.layout.data_directories["requirements"] / "v000001.json"
    path.write_text('{"tampered":true}\n', encoding="utf-8")

    with pytest.raises(
        RequirementVersionConflictError,
        match="hash mismatch",
    ):
        store.open_version(handle.project_id, 1)


def test_tampered_current_hash_is_detected(tmp_path: Path) -> None:
    store, _projects, handle = make_version_store(tmp_path)
    store.create_initial(handle.project_id, ["A"])
    path = handle.layout.data_directories["requirements"] / "current.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["snapshot_sha256"] = "0" * 64
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(
        RequirementVersionConflictError,
        match="current pointer hash mismatch",
    ):
        store.open_latest(handle.project_id)


def test_missing_snapshot_hash_fails_closed(tmp_path: Path) -> None:
    store, _projects, handle = make_version_store(tmp_path)
    store.create_initial(handle.project_id, ["A"])
    directory = handle.layout.data_directories["requirements"]
    (directory / "v000001.json.sha256").unlink()

    with pytest.raises(RequirementVersionNotFoundError):
        store.open_version(handle.project_id, 1)


def test_snapshot_symlink_is_rejected(tmp_path: Path) -> None:
    store, _projects, handle = make_version_store(tmp_path)
    store.create_initial(handle.project_id, ["A"])
    directory = handle.layout.data_directories["requirements"]
    snapshot = directory / "v000001.json"
    external = tmp_path / "external.json"
    external.write_bytes(snapshot.read_bytes())
    snapshot.unlink()
    snapshot.symlink_to(external)

    with pytest.raises(
        RequirementVersionConflictError,
        match="symbolic link",
    ):
        store.open_version(handle.project_id, 1)


def test_current_pointer_duplicate_key_is_rejected(tmp_path: Path) -> None:
    store, _projects, handle = make_version_store(tmp_path)
    store.create_initial(handle.project_id, ["A"])
    current = handle.layout.data_directories["requirements"] / "current.json"
    current.write_text(
        '{"format_version":1,"project_id":"UAV-PROJ-000001",'
        '"latest_version":1,"latest_version":1,'
        '"snapshot_sha256":"' + "0" * 64 + '"}',
        encoding="utf-8",
    )

    with pytest.raises(
        RequirementVersionConflictError,
        match="duplicate JSON key",
    ):
        store.open_latest(handle.project_id)


def test_write_failure_rolls_back_sequence_and_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, projects, handle = make_version_store(tmp_path)

    def fail_new(path, raw, *, mode=0o600):
        raise OSError("injected write failure")

    monkeypatch.setattr(version_module, "_atomic_write_new", fail_new)

    with pytest.raises(OSError, match="injected"):
        store.create_initial(handle.project_id, ["A"])

    directory = handle.layout.data_directories["requirements"]
    assert not (directory / "v000001.json").exists()
    assert not (directory / "current.json").exists()

    with sqlite3.connect(projects.database_path) as connection:
        seq = connection.execute(
            "SELECT last_sequence FROM canonical_id_sequences WHERE kind='requirement'"
        ).fetchone()[0]
    assert seq == 0


def test_current_write_failure_restores_previous_version_and_sequence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, projects, handle = make_version_store(tmp_path)
    first = store.create_initial(handle.project_id, ["A"])
    original_replace = version_module._atomic_replace

    def fail_current(path, raw, *, mode=0o600):
        if path.name == "current.json":
            raise OSError("injected current failure")
        return original_replace(path, raw, mode=mode)

    monkeypatch.setattr(version_module, "_atomic_replace", fail_current)

    with pytest.raises(OSError, match="injected"):
        store.append_version(handle.project_id, ["B"])

    # Restore helper so the verification call itself is not injected.
    monkeypatch.setattr(version_module, "_atomic_replace", original_replace)

    assert store.open_latest(handle.project_id) == first
    directory = handle.layout.data_directories["requirements"]
    assert not (directory / "v000002.json").exists()
    assert not (directory / "v000002.json.sha256").exists()

    with sqlite3.connect(projects.database_path) as connection:
        seq = connection.execute(
            "SELECT last_sequence FROM canonical_id_sequences WHERE kind='requirement'"
        ).fetchone()[0]
    assert seq == 1


def test_restart_does_not_rewrite_old_snapshot(tmp_path: Path) -> None:
    store, projects, handle = make_version_store(tmp_path)
    store.create_initial(handle.project_id, ["A"])
    path = handle.layout.data_directories["requirements"] / "v000001.json"
    before = path.read_bytes()

    restarted = RequirementVersionStore(ProjectStore(projects.projects_root))
    restarted.open_version(handle.project_id, 1)

    assert path.read_bytes() == before


def test_store_exposes_no_general_transaction_or_impact_api(tmp_path: Path) -> None:
    store, _projects, _handle = make_version_store(tmp_path)
    for name in (
        "transaction",
        "begin",
        "commit",
        "rollback",
        "change_request",
        "impact_analysis",
        "mark_stale",
        "invalidate_artifacts",
    ):
        assert not hasattr(store, name)


def test_module_has_no_ai_network_or_uav_runtime_imports() -> None:
    text = Path(version_module.__file__).read_text(encoding="utf-8").lower()
    for forbidden in (
        "ollama",
        "requests",
        "httpx",
        "mavsdk",
        "px4",
        "gazebo",
    ):
        assert forbidden not in text


def test_module_has_no_retry_or_sleep_loop() -> None:
    text = Path(version_module.__file__).read_text(encoding="utf-8")
    assert "while True" not in text
    assert "time.sleep" not in text
    assert "asyncio.sleep" not in text
