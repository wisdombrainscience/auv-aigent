from __future__ import annotations

from pathlib import Path
import sqlite3

import pytest

from ai.artifacts import (
    ARTIFACT_REGISTRY_DB_FILENAME,
    ARTIFACT_REGISTRY_SCHEMA_VERSION,
    ARTIFACT_VERSION_STORE_CONTRACT_VERSION,
    MAX_ARTIFACT_RECORD_JSON_BYTES,
    ArtifactInputVersion,
    ArtifactVersionConflictError,
    ArtifactVersionInput,
    ArtifactVersionNotFoundError,
    ArtifactVersionStore,
)
from ai.contracts.artifact_states import ArtifactState
from ai.contracts.ids import IdKind
from ai.project.schema import PROJECT_SCHEMA_TABLES
from ai.project.store import ProjectStore


def make_store(tmp_path: Path):
    projects = ProjectStore(
        tmp_path / "remote-uav" / "projects"
    )
    handle = projects.create_project()
    return ArtifactVersionStore(projects), projects, handle


def version_input(
    *,
    artifact_type: str = "mass_budget",
    producer_task: str = "TASK-000001",
    content: bytes = b"artifact-content-a",
    timestamp: str = "2026-10-03T18:30:00Z",
    dependencies=(),
) -> ArtifactVersionInput:
    return ArtifactVersionInput(
        type=artifact_type,
        producer_task=producer_task,
        input_dependency_versions=tuple(dependencies),
        content=content,
        created_timestamp=timestamp,
    )


def test_contract_constants_are_explicit() -> None:
    assert ARTIFACT_VERSION_STORE_CONTRACT_VERSION == "1.4"
    assert ARTIFACT_REGISTRY_SCHEMA_VERSION == 4
    assert ARTIFACT_REGISTRY_DB_FILENAME == "artifact_registry.sqlite3"
    assert MAX_ARTIFACT_RECORD_JSON_BYTES == 512 * 1024


def test_create_allocates_artifact_id_and_version_one(tmp_path: Path) -> None:
    store, _projects, handle = make_store(tmp_path)

    record = store.create_artifact(
        handle.project_id,
        version_input(),
    )

    assert record.artifact_id.value == "ART-000001"
    assert record.version == 1
    assert record.project_id == handle.project_id
    assert record.status is ArtifactState.DRAFT
    assert record.validation_result is None


def test_artifact_ids_are_globally_allocated_across_projects(
    tmp_path: Path,
) -> None:
    projects = ProjectStore(
        tmp_path / "remote-uav" / "projects"
    )
    first = projects.create_project()
    second = projects.create_project()
    store = ArtifactVersionStore(projects)

    a = store.create_artifact(first.project_id, version_input())
    b = store.create_artifact(
        second.project_id,
        version_input(
            producer_task="TASK-000002",
            content=b"artifact-content-b",
        ),
    )

    assert a.artifact_id.value == "ART-000001"
    assert b.artifact_id.value == "ART-000002"


def test_create_updates_existing_phase3_artifact_sequence(
    tmp_path: Path,
) -> None:
    store, projects, handle = make_store(tmp_path)
    store.create_artifact(handle.project_id, version_input())

    with sqlite3.connect(projects.database_path) as connection:
        row = connection.execute(
            """
            SELECT last_sequence
            FROM canonical_id_sequences
            WHERE kind=?
            """,
            (IdKind.ARTIFACT.value,),
        ).fetchone()

    assert row == (1,)


def test_registry_is_per_project_and_has_only_a4_2_tables(
    tmp_path: Path,
) -> None:
    store, _projects, handle = make_store(tmp_path)
    store.create_artifact(handle.project_id, version_input())

    path = handle.layout.project_dir / ARTIFACT_REGISTRY_DB_FILENAME
    with sqlite3.connect(path) as connection:
        tables = tuple(
            row[0]
            for row in connection.execute(
                """
                SELECT name
                FROM sqlite_master
                WHERE type='table' AND name NOT LIKE 'sqlite_%'
                ORDER BY name
                """
            )
        )

    assert tables == (
        "artifact_dependency_edges",
        "artifact_heads",
        "artifact_lifecycle",
        "artifact_registry_meta",
        "artifact_supersession",
        "artifact_versions",
    )


def test_a4_2_does_not_change_phase3_sqlite_schema(tmp_path: Path) -> None:
    store, projects, handle = make_store(tmp_path)
    store.create_artifact(handle.project_id, version_input())

    with sqlite3.connect(projects.database_path) as connection:
        tables = tuple(
            row[0]
            for row in connection.execute(
                """
                SELECT name
                FROM sqlite_master
                WHERE type='table' AND name NOT LIKE 'sqlite_%'
                ORDER BY name
                """
            )
        )

    assert tables == PROJECT_SCHEMA_TABLES


def test_append_increments_exactly_one_and_preserves_id(
    tmp_path: Path,
) -> None:
    store, _projects, handle = make_store(tmp_path)
    first = store.create_artifact(
        handle.project_id,
        version_input(),
    )
    second = store.append_version(
        handle.project_id,
        first.artifact_id,
        version_input(
            producer_task="TASK-000002",
            content=b"artifact-content-b",
            timestamp="2026-10-03T18:31:00Z",
        ),
    )

    assert second.artifact_id == first.artifact_id
    assert second.version == 2
    assert second.status is ArtifactState.DRAFT
    assert second.validation_result is None


def test_old_versions_remain_readable(tmp_path: Path) -> None:
    store, _projects, handle = make_store(tmp_path)
    first = store.create_artifact(
        handle.project_id,
        version_input(content=b"artifact-content-a"),
    )
    second = store.append_version(
        handle.project_id,
        first.artifact_id,
        version_input(
            producer_task="TASK-000002",
            content=b"artifact-content-b",
            timestamp="2026-10-03T18:31:00Z",
        ),
    )

    assert store.open_version(
        handle.project_id,
        first.artifact_id,
        1,
    ) == first
    assert store.open_version(
        handle.project_id,
        first.artifact_id,
        2,
    ) == second
    assert store.open_latest(
        handle.project_id,
        first.artifact_id,
    ) == second
    assert store.list_versions(
        handle.project_id,
        first.artifact_id,
    ) == (1, 2)


def test_restart_reopens_same_history(tmp_path: Path) -> None:
    store, projects, handle = make_store(tmp_path)
    first = store.create_artifact(
        handle.project_id,
        version_input(),
    )
    second = store.append_version(
        handle.project_id,
        first.artifact_id,
        version_input(
            content=b"artifact-content-b",
            timestamp="2026-10-03T18:31:00Z",
        ),
    )

    restarted = ArtifactVersionStore(
        ProjectStore(projects.projects_root)
    )
    assert restarted.open_version(
        handle.project_id,
        first.artifact_id,
        1,
    ) == first
    assert restarted.open_latest(
        handle.project_id,
        first.artifact_id,
    ) == second


def test_append_requires_existing_artifact(tmp_path: Path) -> None:
    store, _projects, handle = make_store(tmp_path)

    with pytest.raises(ArtifactVersionNotFoundError):
        store.append_version(
            handle.project_id,
            "ART-000001",
            version_input(),
        )


def test_open_missing_registry_is_not_found(tmp_path: Path) -> None:
    store, _projects, handle = make_store(tmp_path)

    with pytest.raises(ArtifactVersionNotFoundError):
        store.open_latest(
            handle.project_id,
            "ART-000001",
        )


def test_artifact_type_is_stable_across_versions(tmp_path: Path) -> None:
    store, _projects, handle = make_store(tmp_path)
    first = store.create_artifact(
        handle.project_id,
        version_input(artifact_type="mass_budget"),
    )

    with pytest.raises(
        ArtifactVersionConflictError,
        match="type must remain stable",
    ):
        store.append_version(
            handle.project_id,
            first.artifact_id,
            version_input(
                artifact_type="power_budget",
                content=b"artifact-content-b",
                timestamp="2026-10-03T18:31:00Z",
            ),
        )


def test_append_may_change_producer_hash_and_dependencies(
    tmp_path: Path,
) -> None:
    store, _projects, handle = make_store(tmp_path)
    first = store.create_artifact(
        handle.project_id,
        version_input(),
    )
    second = store.append_version(
        handle.project_id,
        first.artifact_id,
        version_input(
            producer_task="TASK-000002",
            content=b"artifact-content-c",
            timestamp="2026-10-03T18:32:00Z",
            dependencies=(
                ArtifactInputVersion(
                    entity_id=first.artifact_id,
                    version=1,
                ),
            ),
        ),
    )

    assert second.producer_task.value == "TASK-000002"
    from ai.artifacts import compute_artifact_sha256
    assert second.hash == compute_artifact_sha256(b"artifact-content-c")
    assert second.input_dependency_versions == (
        ArtifactInputVersion(
            entity_id=first.artifact_id,
            version=1,
        ),
    )


def test_a4_3_validates_artifact_dependency_existence(
    tmp_path: Path,
) -> None:
    store, _projects, handle = make_store(tmp_path)

    with pytest.raises(
        ArtifactVersionConflictError,
        match="does not exist",
    ):
        store.create_artifact(
            handle.project_id,
            version_input(
                dependencies=(
                    ArtifactInputVersion(
                        entity_id="ART-999999",
                        version=999999,
                    ),
                )
            ),
        )


def test_a4_4_computes_hash_from_content(tmp_path: Path) -> None:
    from ai.artifacts import compute_artifact_sha256

    store, _projects, handle = make_store(tmp_path)
    content = b"artifact-content-d"

    record = store.create_artifact(
        handle.project_id,
        version_input(content=content),
    )
    assert record.hash == compute_artifact_sha256(content)


def test_a4_2_forces_new_version_to_draft_without_validation(
    tmp_path: Path,
) -> None:
    store, _projects, handle = make_store(tmp_path)
    first = store.create_artifact(
        handle.project_id,
        version_input(),
    )
    second = store.append_version(
        handle.project_id,
        first.artifact_id,
        version_input(
            content=b"artifact-content-e",
            timestamp="2026-10-03T18:35:00Z",
        ),
    )

    assert first.status is ArtifactState.DRAFT
    assert second.status is ArtifactState.DRAFT
    assert first.validation_result is None
    assert second.validation_result is None


def test_registry_corrupt_head_fails_closed(tmp_path: Path) -> None:
    store, _projects, handle = make_store(tmp_path)
    first = store.create_artifact(
        handle.project_id,
        version_input(),
    )
    path = handle.layout.project_dir / ARTIFACT_REGISTRY_DB_FILENAME

    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA foreign_keys=OFF")
        connection.execute(
            """
            UPDATE artifact_heads
            SET latest_version=2
            WHERE artifact_id=?
            """,
            (first.artifact_id.value,),
        )
        connection.commit()

    with pytest.raises(
        ArtifactVersionConflictError,
        match="head/version history mismatch",
    ):
        store.open_latest(
            handle.project_id,
            first.artifact_id,
        )


def test_registry_record_json_corruption_fails_closed(tmp_path: Path) -> None:
    store, _projects, handle = make_store(tmp_path)
    first = store.create_artifact(
        handle.project_id,
        version_input(),
    )
    path = handle.layout.project_dir / ARTIFACT_REGISTRY_DB_FILENAME

    with sqlite3.connect(path) as connection:
        connection.execute(
            """
            UPDATE artifact_versions
            SET record_json='{"bad":true}'
            WHERE artifact_id=? AND version=1
            """,
            (first.artifact_id.value,),
        )
        connection.commit()

    with pytest.raises(ArtifactVersionConflictError):
        store.open_version(
            handle.project_id,
            first.artifact_id,
            1,
        )


def test_registry_symlink_fails_closed(tmp_path: Path) -> None:
    store, _projects, handle = make_store(tmp_path)
    path = handle.layout.project_dir / ARTIFACT_REGISTRY_DB_FILENAME
    external = tmp_path / "outside.sqlite3"
    external.write_bytes(b"")
    path.symlink_to(external)

    with pytest.raises(
        ArtifactVersionConflictError,
        match="symbolic link",
    ):
        store.create_artifact(
            handle.project_id,
            version_input(),
        )


def test_failed_initial_insert_does_not_advance_artifact_sequence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, projects, handle = make_store(tmp_path)

    # Create registry then corrupt its versions table so insert fails inside
    # the attached multi-database transaction.
    path = handle.layout.project_dir / ARTIFACT_REGISTRY_DB_FILENAME
    from ai.artifacts.versions import initialize_artifact_registry
    initialize_artifact_registry(path)

    with sqlite3.connect(path) as connection:
        connection.execute("DROP TABLE artifact_heads")
        connection.commit()

    with pytest.raises(ArtifactVersionConflictError):
        store.create_artifact(
            handle.project_id,
            version_input(),
        )

    with sqlite3.connect(projects.database_path) as connection:
        row = connection.execute(
            """
            SELECT last_sequence
            FROM canonical_id_sequences
            WHERE kind=?
            """,
            (IdKind.ARTIFACT.value,),
        ).fetchone()

    assert row == (0,)


def test_same_artifact_id_is_not_visible_in_other_project(
    tmp_path: Path,
) -> None:
    projects = ProjectStore(
        tmp_path / "remote-uav" / "projects"
    )
    first = projects.create_project()
    second = projects.create_project()
    store = ArtifactVersionStore(projects)

    record = store.create_artifact(
        first.project_id,
        version_input(),
    )

    with pytest.raises(ArtifactVersionNotFoundError):
        store.open_latest(
            second.project_id,
            record.artifact_id,
        )


def test_a4_2_exposes_no_state_transition_or_graph_api(
    tmp_path: Path,
) -> None:
    store, _projects, _handle = make_store(tmp_path)

    for name in (
        "transition",
        "approve",
        "review",
        "validate",
        "mark_stale",
        "supersede",
        "add_edge",
        "dependencies",
        "descendants",
        "compute_hash",
    ):
        assert not hasattr(store, name)
