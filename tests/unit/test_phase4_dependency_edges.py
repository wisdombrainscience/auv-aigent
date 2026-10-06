from __future__ import annotations

from pathlib import Path
import sqlite3

import pytest

from ai.artifacts import (
    ARTIFACT_DEPENDENCY_EDGE_CONTRACT_VERSION,
    ARTIFACT_LINEAGE_STORE_CONTRACT_VERSION,
    ARTIFACT_REGISTRY_DB_FILENAME,
    ARTIFACT_REGISTRY_SCHEMA_VERSION,
    ArtifactInputVersion,
    ArtifactLineageConflictError,
    ArtifactLineageStore,
    ArtifactVersionConflictError,
    ArtifactVersionInput,
    ArtifactVersionStore,
)
from ai.project.requirement_versions import (
    RequirementVersionInput,
    RequirementVersionStore,
)
from ai.project.schema import PROJECT_SCHEMA_TABLES
from ai.project.store import ProjectStore


def make_project(tmp_path: Path):
    projects = ProjectStore(
        tmp_path / "remote-uav" / "projects"
    )
    handle = projects.create_project()
    requirements = RequirementVersionStore(projects)
    req_v1 = requirements.create_initial(
        handle.project_id,
        [
            "Payload shall not exceed 2 kg.",
            "Endurance shall be at least 20 minutes.",
        ],
    )
    req_v2 = requirements.append_version(
        handle.project_id,
        [
            RequirementVersionInput(
                requirement_id=req_v1.requirement_set.requirements[0].requirement_id,
                text="Payload shall not exceed 2.2 kg.",
            ),
            RequirementVersionInput(
                requirement_id=req_v1.requirement_set.requirements[1].requirement_id,
                text="Endurance shall be at least 20 minutes.",
            ),
        ],
    )
    return projects, handle, req_v1, req_v2


def input_for(
    *,
    artifact_type="mass_budget",
    producer="TASK-000001",
    content=b"artifact-content-a",
    timestamp="2026-10-03T19:30:00Z",
    dependencies=(),
):
    return ArtifactVersionInput(
        type=artifact_type,
        producer_task=producer,
        input_dependency_versions=tuple(dependencies),
        content=content,
        created_timestamp=timestamp,
    )


def test_contract_versions_are_explicit() -> None:
    assert ARTIFACT_DEPENDENCY_EDGE_CONTRACT_VERSION == "1.0"
    assert ARTIFACT_LINEAGE_STORE_CONTRACT_VERSION == "1.0"
    assert ARTIFACT_REGISTRY_SCHEMA_VERSION == 4


def test_requirement_dependency_must_exist_in_referenced_requirement_version(
    tmp_path: Path,
) -> None:
    projects, handle, req_v1, req_v2 = make_project(tmp_path)
    req_id = req_v1.requirement_set.requirements[0].requirement_id
    store = ArtifactVersionStore(projects)

    record = store.create_artifact(
        handle.project_id,
        input_for(
            dependencies=(
                ArtifactInputVersion(
                    entity_id=req_id,
                    version=req_v2.version,
                ),
            )
        ),
    )

    assert record.input_dependency_versions[0].entity_id == req_id


def test_missing_requirement_version_is_rejected(tmp_path: Path) -> None:
    projects, handle, req_v1, _req_v2 = make_project(tmp_path)
    req_id = req_v1.requirement_set.requirements[0].requirement_id

    with pytest.raises(
        ArtifactVersionConflictError,
        match="requirement dependency version",
    ):
        ArtifactVersionStore(projects).create_artifact(
            handle.project_id,
            input_for(
                dependencies=(
                    ArtifactInputVersion(
                        entity_id=req_id,
                        version=999,
                    ),
                )
            ),
        )


def test_requirement_removed_from_version_is_rejected(tmp_path: Path) -> None:
    projects = ProjectStore(
        tmp_path / "remote-uav" / "projects"
    )
    handle = projects.create_project()
    reqs = RequirementVersionStore(projects)
    first = reqs.create_initial(handle.project_id, ["A", "B"])
    removed = first.requirement_set.requirements[1].requirement_id
    second = reqs.append_version(
        handle.project_id,
        [
            RequirementVersionInput(
                requirement_id=first.requirement_set.requirements[0].requirement_id,
                text="A2",
            ),
        ],
    )

    with pytest.raises(
        ArtifactVersionConflictError,
        match="not present",
    ):
        ArtifactVersionStore(projects).create_artifact(
            handle.project_id,
            input_for(
                dependencies=(
                    ArtifactInputVersion(
                        entity_id=removed,
                        version=second.version,
                    ),
                )
            ),
        )


def test_artifact_dependency_must_exist_before_child(tmp_path: Path) -> None:
    projects, handle, _r1, _r2 = make_project(tmp_path)

    with pytest.raises(
        ArtifactVersionConflictError,
        match="does not exist",
    ):
        ArtifactVersionStore(projects).create_artifact(
            handle.project_id,
            input_for(
                dependencies=(
                    ArtifactInputVersion(
                        entity_id="ART-999999",
                        version=1,
                    ),
                )
            ),
        )


def test_dependency_edges_persist_in_record_order(tmp_path: Path) -> None:
    projects, handle, req_v1, req_v2 = make_project(tmp_path)
    req_id = req_v1.requirement_set.requirements[0].requirement_id
    versions = ArtifactVersionStore(projects)

    source = versions.create_artifact(
        handle.project_id,
        input_for(
            artifact_type="requirements_summary",
            content=b"artifact-content-b",
        ),
    )
    child = versions.create_artifact(
        handle.project_id,
        input_for(
            artifact_type="mass_budget",
            producer="TASK-000002",
            content=b"artifact-content-c",
            timestamp="2026-10-03T19:31:00Z",
            dependencies=(
                ArtifactInputVersion(
                    entity_id=req_id,
                    version=req_v2.version,
                ),
                ArtifactInputVersion(
                    entity_id=source.artifact_id,
                    version=source.version,
                ),
            ),
        ),
    )

    lineage = ArtifactLineageStore(projects)
    assert lineage.dependencies(
        handle.project_id,
        child.artifact_id,
        child.version,
    ) == child.input_dependency_versions


def test_direct_dependents_support_requirement_and_artifact_edges(
    tmp_path: Path,
) -> None:
    projects, handle, req_v1, req_v2 = make_project(tmp_path)
    req_id = req_v1.requirement_set.requirements[0].requirement_id
    versions = ArtifactVersionStore(projects)

    source = versions.create_artifact(
        handle.project_id,
        input_for(artifact_type="source", content=b"artifact-content-b"),
    )
    child = versions.create_artifact(
        handle.project_id,
        input_for(
            artifact_type="child",
            producer="TASK-000002",
            content=b"artifact-content-c",
            timestamp="2026-10-03T19:31:00Z",
            dependencies=(
                ArtifactInputVersion(
                    entity_id=req_id,
                    version=req_v2.version,
                ),
                ArtifactInputVersion(
                    entity_id=source.artifact_id,
                    version=1,
                ),
            ),
        ),
    )

    lineage = ArtifactLineageStore(projects)
    req_dependents = lineage.direct_dependents(
        handle.project_id,
        ArtifactInputVersion(
            entity_id=req_id,
            version=req_v2.version,
        ),
    )
    art_dependents = lineage.direct_dependents(
        handle.project_id,
        ArtifactInputVersion(
            entity_id=source.artifact_id,
            version=1,
        ),
    )

    assert [(x.artifact_id.value, x.version) for x in req_dependents] == [
        (child.artifact_id.value, child.version)
    ]
    assert [(x.artifact_id.value, x.version) for x in art_dependents] == [
        (child.artifact_id.value, child.version)
    ]


def test_append_can_depend_on_previous_version_of_same_logical_artifact(
    tmp_path: Path,
) -> None:
    projects, handle, _r1, _r2 = make_project(tmp_path)
    versions = ArtifactVersionStore(projects)

    first = versions.create_artifact(
        handle.project_id,
        input_for(),
    )
    second = versions.append_version(
        handle.project_id,
        first.artifact_id,
        input_for(
            producer="TASK-000002",
            content=b"artifact-content-b",
            timestamp="2026-10-03T19:31:00Z",
            dependencies=(
                ArtifactInputVersion(
                    entity_id=first.artifact_id,
                    version=1,
                ),
            ),
        ),
    )

    assert second.version == 2


def test_append_cannot_depend_on_version_currently_being_created(
    tmp_path: Path,
) -> None:
    projects, handle, _r1, _r2 = make_project(tmp_path)
    versions = ArtifactVersionStore(projects)

    first = versions.create_artifact(
        handle.project_id,
        input_for(),
    )

    with pytest.raises(
        ArtifactVersionConflictError,
        match="does not exist",
    ):
        versions.append_version(
            handle.project_id,
            first.artifact_id,
            input_for(
                content=b"artifact-content-b",
                timestamp="2026-10-03T19:31:00Z",
                dependencies=(
                    ArtifactInputVersion(
                        entity_id=first.artifact_id,
                        version=2,
                    ),
                ),
            ),
        )


def test_cross_project_artifact_dependency_is_rejected(tmp_path: Path) -> None:
    projects = ProjectStore(
        tmp_path / "remote-uav" / "projects"
    )
    first = projects.create_project()
    second = projects.create_project()
    versions = ArtifactVersionStore(projects)

    source = versions.create_artifact(
        first.project_id,
        input_for(),
    )

    with pytest.raises(
        ArtifactVersionConflictError,
        match="does not exist",
    ):
        versions.create_artifact(
            second.project_id,
            input_for(
                artifact_type="child",
                content=b"artifact-content-b",
                dependencies=(
                    ArtifactInputVersion(
                        entity_id=source.artifact_id,
                        version=1,
                    ),
                ),
            ),
        )


def test_edges_are_immutable_with_artifact_version(tmp_path: Path) -> None:
    projects, handle, req_v1, _req_v2 = make_project(tmp_path)
    req_id = req_v1.requirement_set.requirements[0].requirement_id
    versions = ArtifactVersionStore(projects)
    record = versions.create_artifact(
        handle.project_id,
        input_for(
            dependencies=(
                ArtifactInputVersion(
                    entity_id=req_id,
                    version=1,
                ),
            )
        ),
    )

    path = handle.layout.project_dir / ARTIFACT_REGISTRY_DB_FILENAME
    with sqlite3.connect(path) as connection:
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                INSERT INTO artifact_dependency_edges(
                    child_artifact_id,
                    child_version,
                    ordinal,
                    dependency_kind,
                    dependency_id,
                    dependency_version
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    record.artifact_id.value,
                    record.version,
                    0,
                    "requirement",
                    req_id.value,
                    1,
                ),
            )


def test_tampered_edge_record_mismatch_fails_closed(tmp_path: Path) -> None:
    projects, handle, req_v1, _req_v2 = make_project(tmp_path)
    req_id = req_v1.requirement_set.requirements[0].requirement_id
    versions = ArtifactVersionStore(projects)
    record = versions.create_artifact(
        handle.project_id,
        input_for(
            dependencies=(
                ArtifactInputVersion(
                    entity_id=req_id,
                    version=1,
                ),
            )
        ),
    )
    path = handle.layout.project_dir / ARTIFACT_REGISTRY_DB_FILENAME

    with sqlite3.connect(path) as connection:
        connection.execute(
            """
            UPDATE artifact_dependency_edges
            SET dependency_version=2
            WHERE child_artifact_id=? AND child_version=1
            """,
            (record.artifact_id.value,),
        )
        connection.commit()

    with pytest.raises(
        ArtifactVersionConflictError,
        match="do not match immutable record",
    ):
        ArtifactLineageStore(projects).dependencies(
            handle.project_id,
            record.artifact_id,
            1,
        )


def test_restart_preserves_lineage_and_verify_project(tmp_path: Path) -> None:
    projects, handle, req_v1, req_v2 = make_project(tmp_path)
    req_id = req_v1.requirement_set.requirements[0].requirement_id
    versions = ArtifactVersionStore(projects)

    source = versions.create_artifact(
        handle.project_id,
        input_for(artifact_type="source", content=b"artifact-content-b"),
    )
    child = versions.create_artifact(
        handle.project_id,
        input_for(
            artifact_type="child",
            content=b"artifact-content-c",
            timestamp="2026-10-03T19:31:00Z",
            dependencies=(
                ArtifactInputVersion(
                    entity_id=req_id,
                    version=req_v2.version,
                ),
                ArtifactInputVersion(
                    entity_id=source.artifact_id,
                    version=1,
                ),
            ),
        ),
    )

    restarted_projects = ProjectStore(projects.projects_root)
    lineage = ArtifactLineageStore(restarted_projects)
    lineage.verify_project(handle.project_id)

    assert lineage.dependencies(
        handle.project_id,
        child.artifact_id,
        1,
    ) == child.input_dependency_versions


def test_a4_3_registry_has_four_tables_and_phase3_schema_is_unchanged(
    tmp_path: Path,
) -> None:
    projects, handle, _r1, _r2 = make_project(tmp_path)
    ArtifactVersionStore(projects).create_artifact(
        handle.project_id,
        input_for(),
    )

    registry = handle.layout.project_dir / ARTIFACT_REGISTRY_DB_FILENAME
    with sqlite3.connect(registry) as connection:
        registry_tables = tuple(
            row[0]
            for row in connection.execute(
                """
                SELECT name FROM sqlite_master
                WHERE type='table' AND name NOT LIKE 'sqlite_%'
                ORDER BY name
                """
            )
        )

    with sqlite3.connect(projects.database_path) as connection:
        project_tables = tuple(
            row[0]
            for row in connection.execute(
                """
                SELECT name FROM sqlite_master
                WHERE type='table' AND name NOT LIKE 'sqlite_%'
                ORDER BY name
                """
            )
        )

    assert registry_tables == (
        "artifact_dependency_edges",
        "artifact_heads",
        "artifact_lifecycle",
        "artifact_registry_meta",
        "artifact_supersession",
        "artifact_versions",
    )
    assert project_tables == PROJECT_SCHEMA_TABLES


def test_lineage_api_is_read_only_and_does_not_propagate_state(
    tmp_path: Path,
) -> None:
    projects, _handle, _r1, _r2 = make_project(tmp_path)
    lineage = ArtifactLineageStore(projects)

    for name in (
        "add_edge",
        "remove_edge",
        "mark_stale",
        "supersede",
        "transition",
        "compute_hash",
    ):
        assert not hasattr(lineage, name)


def test_clean_a4_2_registry_migrates_to_schema_v2_with_edges(
    tmp_path: Path,
) -> None:
    from ai.artifacts.schema import ArtifactRecord
    from ai.artifacts.versions import initialize_artifact_registry
    from ai.contracts.artifact_states import ArtifactState
    import json

    projects = ProjectStore(
        tmp_path / "remote-uav" / "projects"
    )
    handle = projects.create_project()
    path = handle.layout.project_dir / ARTIFACT_REGISTRY_DB_FILENAME

    source = ArtifactRecord(
        artifact_id="ART-000001",
        project_id=handle.project_id,
        version=1,
        type="source",
        producer_task="TASK-000001",
        input_dependency_versions=(),
        hash="a" * 64,
        status=ArtifactState.DRAFT,
        validation_result=None,
        created_timestamp="2026-10-03T19:20:00Z",
    )
    child = ArtifactRecord(
        artifact_id="ART-000002",
        project_id=handle.project_id,
        version=1,
        type="child",
        producer_task="TASK-000002",
        input_dependency_versions=(
            ArtifactInputVersion(
                entity_id=source.artifact_id,
                version=1,
            ),
        ),
        hash="b" * 64,
        status=ArtifactState.DRAFT,
        validation_result=None,
        created_timestamp="2026-10-03T19:21:00Z",
    )

    def record_json(record):
        return (
            json.dumps(
                record.to_dict(),
                ensure_ascii=False,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        )

    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute(
            """
            CREATE TABLE artifact_registry_meta (
                singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
                schema_version INTEGER NOT NULL CHECK (schema_version = 1)
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE artifact_versions (
                artifact_id TEXT NOT NULL,
                version INTEGER NOT NULL,
                project_id TEXT NOT NULL,
                artifact_type TEXT NOT NULL,
                record_json TEXT NOT NULL,
                PRIMARY KEY (artifact_id, version)
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE artifact_heads (
                artifact_id TEXT PRIMARY KEY NOT NULL,
                latest_version INTEGER NOT NULL,
                FOREIGN KEY (artifact_id, latest_version)
                    REFERENCES artifact_versions(artifact_id, version)
                    DEFERRABLE INITIALLY DEFERRED
            )
            """
        )
        connection.execute(
            "INSERT INTO artifact_registry_meta VALUES (1, 1)"
        )
        for record in (source, child):
            connection.execute(
                """
                INSERT INTO artifact_versions(
                    artifact_id, version, project_id,
                    artifact_type, record_json
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    record.artifact_id.value,
                    record.version,
                    record.project_id.value,
                    record.type,
                    record_json(record),
                ),
            )
            connection.execute(
                "INSERT INTO artifact_heads VALUES (?, ?)",
                (record.artifact_id.value, record.version),
            )
        connection.execute("PRAGMA user_version=1")
        connection.commit()

    initialize_artifact_registry(path)

    with sqlite3.connect(path) as connection:
        tables = tuple(
            row[0]
            for row in connection.execute(
                """
                SELECT name FROM sqlite_master
                WHERE type='table' AND name NOT LIKE 'sqlite_%'
                ORDER BY name
                """
            )
        )
        schema_version = connection.execute(
            "SELECT schema_version FROM artifact_registry_meta WHERE singleton=1"
        ).fetchone()[0]
        user_version = connection.execute(
            "PRAGMA user_version"
        ).fetchone()[0]
        edges = connection.execute(
            """
            SELECT child_artifact_id, child_version,
                   dependency_kind, dependency_id, dependency_version
            FROM artifact_dependency_edges
            ORDER BY child_artifact_id, child_version, ordinal
            """
        ).fetchall()

    assert tables == (
        "artifact_dependency_edges",
        "artifact_heads",
        "artifact_lifecycle",
        "artifact_registry_meta",
        "artifact_supersession",
        "artifact_versions",
    )
    assert schema_version == 4
    assert user_version == 4
    assert edges == [
        ("ART-000002", 1, "artifact", "ART-000001", 1)
    ]
