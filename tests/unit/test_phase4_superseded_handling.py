from __future__ import annotations

from pathlib import Path
import sqlite3

import pytest

from ai.artifacts import (
    ARTIFACT_REGISTRY_DB_FILENAME,
    ARTIFACT_REGISTRY_SCHEMA_VERSION,
    ARTIFACT_SUPERSESSION_CONTRACT_VERSION,
    MAX_AUTO_SUPERSEDED_VERSIONS,
    ArtifactLifecycleConflictError,
    ArtifactLifecycleStore,
    ArtifactSupersessionStore,
    ArtifactVersionInput,
    ArtifactVersionStore,
)
from ai.contracts.artifact_states import ArtifactState
from ai.contracts.validation_result import (
    ValidationCheck,
    ValidationResult,
    ValidationStatus,
)
from ai.project.schema import PROJECT_SCHEMA_TABLES
from ai.project.store import ProjectStore


def pass_validation() -> ValidationResult:
    return ValidationResult.from_checks(
        (
            ValidationCheck(
                check_id="finite-values",
                status=ValidationStatus.PASS,
                field=None,
                reason=None,
                details={},
            ),
        )
    )


def make_project(tmp_path: Path):
    projects = ProjectStore(tmp_path / "remote-uav" / "projects")
    handle = projects.create_project()
    return projects, handle


def input_for(
    *,
    content: bytes,
    task: str,
    timestamp: str,
) -> ArtifactVersionInput:
    return ArtifactVersionInput(
        type="mass_budget",
        producer_task=task,
        input_dependency_versions=(),
        content=content,
        created_timestamp=timestamp,
    )


def advance_to_approved(
    projects: ProjectStore,
    project_id,
    artifact_id,
    version: int,
) -> None:
    lifecycle = ArtifactLifecycleStore(projects)
    lifecycle.transition(project_id, artifact_id, version, ArtifactState.REVIEWED)
    lifecycle.transition(
        project_id,
        artifact_id,
        version,
        ArtifactState.VALIDATED,
        validation_result=pass_validation(),
    )
    lifecycle.transition(project_id, artifact_id, version, ArtifactState.APPROVED)


def test_contract_and_registry_schema_are_explicit() -> None:
    assert ARTIFACT_SUPERSESSION_CONTRACT_VERSION == "1.0"
    assert MAX_AUTO_SUPERSEDED_VERSIONS == 4096
    assert ARTIFACT_REGISTRY_SCHEMA_VERSION == 4


def test_newer_draft_does_not_supersede_current_approved(tmp_path: Path) -> None:
    projects, handle = make_project(tmp_path)
    versions = ArtifactVersionStore(projects)
    first = versions.create_artifact(
        handle.project_id,
        input_for(content=b"v1", task="TASK-000001", timestamp="2026-10-04T03:00:00Z"),
    )
    advance_to_approved(projects, handle.project_id, first.artifact_id, 1)

    second = versions.append_version(
        handle.project_id,
        first.artifact_id,
        input_for(content=b"v2", task="TASK-000002", timestamp="2026-10-04T03:01:00Z"),
    )

    assert versions.open_version(handle.project_id, first.artifact_id, 1).status is ArtifactState.APPROVED
    assert second.status is ArtifactState.DRAFT
    assert ArtifactSupersessionStore(projects).current_approved(
        handle.project_id,
        first.artifact_id,
    ).version == 1


def test_approving_new_version_supersedes_lower_active_version(tmp_path: Path) -> None:
    projects, handle = make_project(tmp_path)
    versions = ArtifactVersionStore(projects)
    first = versions.create_artifact(
        handle.project_id,
        input_for(content=b"v1", task="TASK-000001", timestamp="2026-10-04T03:00:00Z"),
    )
    advance_to_approved(projects, handle.project_id, first.artifact_id, 1)
    second = versions.append_version(
        handle.project_id,
        first.artifact_id,
        input_for(content=b"v2", task="TASK-000002", timestamp="2026-10-04T03:01:00Z"),
    )
    advance_to_approved(projects, handle.project_id, first.artifact_id, 2)

    assert versions.open_version(handle.project_id, first.artifact_id, 1).status is ArtifactState.SUPERSEDED
    assert versions.open_version(handle.project_id, first.artifact_id, 2).status is ArtifactState.APPROVED

    supersession = ArtifactSupersessionStore(projects)
    replacement = supersession.replacement_for(handle.project_id, first.artifact_id, 1)
    assert replacement is not None
    assert replacement.version == 2
    assert [x.version for x in supersession.superseded_by(handle.project_id, first.artifact_id, 2)] == [1]
    assert supersession.current_approved(handle.project_id, first.artifact_id).version == 2


def test_approving_v3_builds_replacement_chain_without_rewriting_old_relation(tmp_path: Path) -> None:
    projects, handle = make_project(tmp_path)
    versions = ArtifactVersionStore(projects)
    first = versions.create_artifact(
        handle.project_id,
        input_for(content=b"v1", task="TASK-000001", timestamp="2026-10-04T03:00:00Z"),
    )
    advance_to_approved(projects, handle.project_id, first.artifact_id, 1)
    versions.append_version(
        handle.project_id,
        first.artifact_id,
        input_for(content=b"v2", task="TASK-000002", timestamp="2026-10-04T03:01:00Z"),
    )
    advance_to_approved(projects, handle.project_id, first.artifact_id, 2)
    versions.append_version(
        handle.project_id,
        first.artifact_id,
        input_for(content=b"v3", task="TASK-000003", timestamp="2026-10-04T03:02:00Z"),
    )
    advance_to_approved(projects, handle.project_id, first.artifact_id, 3)

    store = ArtifactSupersessionStore(projects)
    assert store.replacement_for(handle.project_id, first.artifact_id, 1).version == 2
    assert store.replacement_for(handle.project_id, first.artifact_id, 2).version == 3
    assert store.current_approved(handle.project_id, first.artifact_id).version == 3


def test_historical_lower_version_is_not_rewritten(tmp_path: Path) -> None:
    projects, handle = make_project(tmp_path)
    versions = ArtifactVersionStore(projects)
    first = versions.create_artifact(
        handle.project_id,
        input_for(content=b"v1", task="TASK-000001", timestamp="2026-10-04T03:00:00Z"),
    )
    ArtifactLifecycleStore(projects).transition(
        handle.project_id,
        first.artifact_id,
        1,
        ArtifactState.REJECTED,
    )
    versions.append_version(
        handle.project_id,
        first.artifact_id,
        input_for(content=b"v2", task="TASK-000002", timestamp="2026-10-04T03:01:00Z"),
    )
    advance_to_approved(projects, handle.project_id, first.artifact_id, 2)

    assert versions.open_version(handle.project_id, first.artifact_id, 1).status is ArtifactState.REJECTED
    assert ArtifactSupersessionStore(projects).replacement_for(
        handle.project_id,
        first.artifact_id,
        1,
    ) is None


def test_manual_superseded_transition_remains_legal_low_level_primitive(tmp_path: Path) -> None:
    projects, handle = make_project(tmp_path)
    record = ArtifactVersionStore(projects).create_artifact(
        handle.project_id,
        input_for(content=b"v1", task="TASK-000001", timestamp="2026-10-04T03:00:00Z"),
    )
    result = ArtifactLifecycleStore(projects).transition(
        handle.project_id,
        record.artifact_id,
        1,
        ArtifactState.SUPERSEDED,
    )
    assert result.current_state is ArtifactState.SUPERSEDED
    assert ArtifactSupersessionStore(projects).replacement_for(
        handle.project_id,
        record.artifact_id,
        1,
    ) is None


def test_approval_blocks_unrelated_superseded_artifact_parent(tmp_path: Path) -> None:
    from ai.artifacts import ArtifactInputVersion

    projects, handle = make_project(tmp_path)
    versions = ArtifactVersionStore(projects)
    parent = versions.create_artifact(
        handle.project_id,
        ArtifactVersionInput(
            type="source",
            producer_task="TASK-000001",
            input_dependency_versions=(),
            content=b"parent",
            created_timestamp="2026-10-04T03:00:00Z",
        ),
    )
    ArtifactLifecycleStore(projects).transition(
        handle.project_id,
        parent.artifact_id,
        1,
        ArtifactState.SUPERSEDED,
    )

    child = versions.create_artifact(
        handle.project_id,
        ArtifactVersionInput(
            type="child",
            producer_task="TASK-000002",
            input_dependency_versions=(
                ArtifactInputVersion(entity_id=parent.artifact_id, version=1),
            ),
            content=b"child",
            created_timestamp="2026-10-04T03:01:00Z",
        ),
    )
    lifecycle = ArtifactLifecycleStore(projects)
    lifecycle.transition(handle.project_id, child.artifact_id, 1, ArtifactState.REVIEWED)
    lifecycle.transition(
        handle.project_id,
        child.artifact_id,
        1,
        ArtifactState.VALIDATED,
        validation_result=pass_validation(),
    )
    with pytest.raises(
        ArtifactLifecycleConflictError,
        match="dependency is SUPERSEDED",
    ):
        lifecycle.transition(handle.project_id, child.artifact_id, 1, ArtifactState.APPROVED)


def test_replacement_version_may_depend_on_predecessor_it_supersedes(tmp_path: Path) -> None:
    from ai.artifacts import ArtifactInputVersion

    projects, handle = make_project(tmp_path)
    versions = ArtifactVersionStore(projects)
    first = versions.create_artifact(
        handle.project_id,
        input_for(content=b"v1", task="TASK-000001", timestamp="2026-10-04T03:00:00Z"),
    )
    advance_to_approved(projects, handle.project_id, first.artifact_id, 1)
    versions.append_version(
        handle.project_id,
        first.artifact_id,
        ArtifactVersionInput(
            type="mass_budget",
            producer_task="TASK-000002",
            input_dependency_versions=(
                ArtifactInputVersion(entity_id=first.artifact_id, version=1),
            ),
            content=b"v2",
            created_timestamp="2026-10-04T03:01:00Z",
        ),
    )
    advance_to_approved(projects, handle.project_id, first.artifact_id, 2)
    assert versions.open_version(handle.project_id, first.artifact_id, 1).status is ArtifactState.SUPERSEDED
    assert versions.open_version(handle.project_id, first.artifact_id, 2).status is ArtifactState.APPROVED


def test_restart_preserves_current_and_replacement_metadata(tmp_path: Path) -> None:
    projects, handle = make_project(tmp_path)
    versions = ArtifactVersionStore(projects)
    first = versions.create_artifact(
        handle.project_id,
        input_for(content=b"v1", task="TASK-000001", timestamp="2026-10-04T03:00:00Z"),
    )
    advance_to_approved(projects, handle.project_id, first.artifact_id, 1)
    versions.append_version(
        handle.project_id,
        first.artifact_id,
        input_for(content=b"v2", task="TASK-000002", timestamp="2026-10-04T03:01:00Z"),
    )
    advance_to_approved(projects, handle.project_id, first.artifact_id, 2)

    restarted = ProjectStore(projects.projects_root)
    store = ArtifactSupersessionStore(restarted)
    assert store.current_approved(handle.project_id, first.artifact_id).version == 2
    assert store.replacement_for(handle.project_id, first.artifact_id, 1).version == 2


def test_registry_v4_has_supersession_and_phase3_schema_unchanged(tmp_path: Path) -> None:
    projects, handle = make_project(tmp_path)
    ArtifactVersionStore(projects).create_artifact(
        handle.project_id,
        input_for(content=b"v1", task="TASK-000001", timestamp="2026-10-04T03:00:00Z"),
    )
    registry = handle.layout.project_dir / ARTIFACT_REGISTRY_DB_FILENAME
    with sqlite3.connect(registry) as connection:
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
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 4

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

    assert tables == (
        "artifact_dependency_edges",
        "artifact_heads",
        "artifact_lifecycle",
        "artifact_registry_meta",
        "artifact_supersession",
        "artifact_versions",
    )
    assert project_tables == PROJECT_SCHEMA_TABLES


def test_empty_clean_v3_registry_migrates_to_v4(tmp_path: Path) -> None:
    projects, handle = make_project(tmp_path)
    registry = handle.layout.project_dir / ARTIFACT_REGISTRY_DB_FILENAME

    with sqlite3.connect(registry) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.executescript(
            """
            CREATE TABLE artifact_registry_meta (
                singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
                schema_version INTEGER NOT NULL CHECK (schema_version = 3)
            );
            CREATE TABLE artifact_versions (
                artifact_id TEXT NOT NULL,
                version INTEGER NOT NULL,
                project_id TEXT NOT NULL,
                artifact_type TEXT NOT NULL,
                record_json TEXT NOT NULL,
                PRIMARY KEY (artifact_id, version)
            );
            CREATE TABLE artifact_heads (
                artifact_id TEXT PRIMARY KEY NOT NULL,
                latest_version INTEGER NOT NULL,
                FOREIGN KEY (artifact_id, latest_version)
                    REFERENCES artifact_versions(artifact_id, version)
            );
            CREATE TABLE artifact_dependency_edges (
                child_artifact_id TEXT NOT NULL,
                child_version INTEGER NOT NULL,
                ordinal INTEGER NOT NULL,
                dependency_kind TEXT NOT NULL,
                dependency_id TEXT NOT NULL,
                dependency_version INTEGER NOT NULL,
                PRIMARY KEY (child_artifact_id, child_version, ordinal),
                FOREIGN KEY (child_artifact_id, child_version)
                    REFERENCES artifact_versions(artifact_id, version)
            );
            CREATE TABLE artifact_lifecycle (
                artifact_id TEXT NOT NULL,
                version INTEGER NOT NULL,
                status TEXT NOT NULL,
                validation_result_json TEXT,
                PRIMARY KEY (artifact_id, version),
                FOREIGN KEY (artifact_id, version)
                    REFERENCES artifact_versions(artifact_id, version)
            );
            INSERT INTO artifact_registry_meta VALUES (1, 3);
            PRAGMA user_version=3;
            """
        )
        connection.commit()

    from ai.artifacts import initialize_artifact_registry
    initialize_artifact_registry(registry)

    with sqlite3.connect(registry) as connection:
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
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 4
        assert connection.execute(
            "SELECT schema_version FROM artifact_registry_meta WHERE singleton=1"
        ).fetchone() == (4,)

    assert "artifact_supersession" in tables


def test_tampered_supersession_row_fails_closed(tmp_path: Path) -> None:
    projects, handle = make_project(tmp_path)
    versions = ArtifactVersionStore(projects)
    first = versions.create_artifact(
        handle.project_id,
        input_for(content=b"v1", task="TASK-000001", timestamp="2026-10-04T03:00:00Z"),
    )
    advance_to_approved(projects, handle.project_id, first.artifact_id, 1)
    versions.append_version(
        handle.project_id,
        first.artifact_id,
        input_for(content=b"v2", task="TASK-000002", timestamp="2026-10-04T03:01:00Z"),
    )
    advance_to_approved(projects, handle.project_id, first.artifact_id, 2)

    registry = handle.layout.project_dir / ARTIFACT_REGISTRY_DB_FILENAME
    with sqlite3.connect(registry) as connection:
        connection.execute(
            "UPDATE artifact_lifecycle SET status='STALE' WHERE artifact_id=? AND version=1",
            (first.artifact_id.value,),
        )
        connection.commit()

    with pytest.raises(Exception, match="supersession metadata is inconsistent"):
        ArtifactSupersessionStore(projects).current_approved(
            handle.project_id,
            first.artifact_id,
        )
