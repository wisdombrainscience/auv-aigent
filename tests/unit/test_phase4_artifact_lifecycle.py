from __future__ import annotations

import json
from pathlib import Path
import sqlite3

import pytest

from ai.artifacts import (
    ARTIFACT_LIFECYCLE_CONTRACT_VERSION,
    ARTIFACT_REGISTRY_DB_FILENAME,
    ARTIFACT_REGISTRY_SCHEMA_VERSION,
    ArtifactLifecycleConflictError,
    ArtifactLifecycleStore,
    ArtifactVersionInput,
    ArtifactVersionStore,
    compute_artifact_sha256,
)
from ai.contracts.artifact_states import ArtifactState
from ai.contracts.validation_result import (
    ValidationCheck,
    ValidationResult,
    ValidationStatus,
)
from ai.project.schema import PROJECT_SCHEMA_TABLES
from ai.project.store import ProjectStore


def make_project(tmp_path: Path):
    projects = ProjectStore(
        tmp_path / "remote-uav" / "projects"
    )
    handle = projects.create_project()
    return projects, handle


def artifact_input(
    content: bytes = b"mass-budget-v1",
) -> ArtifactVersionInput:
    return ArtifactVersionInput(
        type="mass_budget",
        producer_task="TASK-000001",
        input_dependency_versions=(),
        content=content,
        created_timestamp="2026-10-03T22:30:00Z",
    )


def pass_validation() -> ValidationResult:
    return ValidationResult.from_checks(
        (
            ValidationCheck.from_dict(
                {
                    "check_id": "mass-budget",
                    "status": "PASS",
                    "field": "vehicle.mass_kg",
                    "reason": None,
                    "details": {
                        "observed": 2.5,
                        "limit": 3.0,
                        "unit": "kg",
                    },
                }
            ),
        )
    )


def fail_validation() -> ValidationResult:
    return ValidationResult.from_checks(
        (
            ValidationCheck.from_dict(
                {
                    "check_id": "mass-budget",
                    "status": "FAIL",
                    "field": "vehicle.mass_kg",
                    "reason": "mass limit exceeded",
                    "details": {
                        "observed": 3.5,
                        "limit": 3.0,
                        "unit": "kg",
                    },
                }
            ),
        )
    )


def create_artifact(projects: ProjectStore, project_id):
    return ArtifactVersionStore(projects).create_artifact(
        project_id,
        artifact_input(),
    )


def test_contract_and_registry_version_are_explicit() -> None:
    assert ARTIFACT_LIFECYCLE_CONTRACT_VERSION == "1.2"
    assert ARTIFACT_REGISTRY_SCHEMA_VERSION == 4


def test_nominal_lifecycle_persists_to_approved(tmp_path: Path) -> None:
    projects, handle = make_project(tmp_path)
    record = create_artifact(projects, handle.project_id)
    lifecycle = ArtifactLifecycleStore(projects)

    reviewed = lifecycle.transition(
        handle.project_id,
        record.artifact_id,
        record.version,
        ArtifactState.REVIEWED,
    )
    assert reviewed.previous_state is ArtifactState.DRAFT
    assert reviewed.current_state is ArtifactState.REVIEWED
    assert reviewed.artifact.validation_result is None

    validated = lifecycle.transition(
        handle.project_id,
        record.artifact_id,
        record.version,
        ArtifactState.VALIDATED,
        validation_result=pass_validation(),
    )
    assert validated.current_state is ArtifactState.VALIDATED
    assert validated.artifact.validation_result is not None
    assert (
        validated.artifact.validation_result.overall
        is ValidationStatus.PASS
    )

    approved = lifecycle.transition(
        handle.project_id,
        record.artifact_id,
        record.version,
        ArtifactState.APPROVED,
    )
    assert approved.current_state is ArtifactState.APPROVED
    assert approved.artifact.validation_result == pass_validation()


def test_restart_open_returns_current_lifecycle_state(tmp_path: Path) -> None:
    projects, handle = make_project(tmp_path)
    record = create_artifact(projects, handle.project_id)
    lifecycle = ArtifactLifecycleStore(projects)

    lifecycle.transition(
        handle.project_id,
        record.artifact_id,
        1,
        ArtifactState.REVIEWED,
    )
    lifecycle.transition(
        handle.project_id,
        record.artifact_id,
        1,
        ArtifactState.VALIDATED,
        validation_result=pass_validation(),
    )

    restarted = ProjectStore(projects.projects_root)
    reopened = ArtifactVersionStore(restarted).open_version(
        handle.project_id,
        record.artifact_id,
        1,
    )
    assert reopened.status is ArtifactState.VALIDATED
    assert reopened.validation_result == pass_validation()


def test_validated_requires_pass_validation_result(tmp_path: Path) -> None:
    projects, handle = make_project(tmp_path)
    record = create_artifact(projects, handle.project_id)
    lifecycle = ArtifactLifecycleStore(projects)
    lifecycle.transition(
        handle.project_id,
        record.artifact_id,
        1,
        ArtifactState.REVIEWED,
    )

    with pytest.raises(
        ArtifactLifecycleConflictError,
        match="overall=PASS",
    ):
        lifecycle.transition(
            handle.project_id,
            record.artifact_id,
            1,
            ArtifactState.VALIDATED,
            validation_result=fail_validation(),
        )


def test_validated_requires_validation_result(tmp_path: Path) -> None:
    projects, handle = make_project(tmp_path)
    record = create_artifact(projects, handle.project_id)
    lifecycle = ArtifactLifecycleStore(projects)
    lifecycle.transition(
        handle.project_id,
        record.artifact_id,
        1,
        ArtifactState.REVIEWED,
    )

    with pytest.raises(
        ArtifactLifecycleConflictError,
        match="requires deterministic ValidationResult",
    ):
        lifecycle.transition(
            handle.project_id,
            record.artifact_id,
            1,
            ArtifactState.VALIDATED,
        )


def test_reviewed_can_be_rejected_with_fail_validation(
    tmp_path: Path,
) -> None:
    projects, handle = make_project(tmp_path)
    record = create_artifact(projects, handle.project_id)
    lifecycle = ArtifactLifecycleStore(projects)

    lifecycle.transition(
        handle.project_id,
        record.artifact_id,
        1,
        ArtifactState.REVIEWED,
    )
    rejected = lifecycle.transition(
        handle.project_id,
        record.artifact_id,
        1,
        ArtifactState.REJECTED,
        validation_result=fail_validation(),
    )

    assert rejected.current_state is ArtifactState.REJECTED
    assert rejected.artifact.validation_result == fail_validation()


def test_rejected_supplied_pass_validation_is_rejected(
    tmp_path: Path,
) -> None:
    projects, handle = make_project(tmp_path)
    record = create_artifact(projects, handle.project_id)

    with pytest.raises(
        ArtifactLifecycleConflictError,
        match="overall=FAIL",
    ):
        ArtifactLifecycleStore(projects).transition(
            handle.project_id,
            record.artifact_id,
            1,
            ArtifactState.REJECTED,
            validation_result=pass_validation(),
        )


def test_approved_requires_prior_validated_pass(tmp_path: Path) -> None:
    projects, handle = make_project(tmp_path)
    record = create_artifact(projects, handle.project_id)
    lifecycle = ArtifactLifecycleStore(projects)

    lifecycle.transition(
        handle.project_id,
        record.artifact_id,
        1,
        ArtifactState.REVIEWED,
    )

    with pytest.raises(
        ArtifactLifecycleConflictError,
        match="REVIEWED -> APPROVED",
    ):
        lifecycle.transition(
            handle.project_id,
            record.artifact_id,
            1,
            ArtifactState.APPROVED,
        )


@pytest.mark.parametrize(
    "target",
    [
        ArtifactState.STALE,
        ArtifactState.SUPERSEDED,
    ],
)
def test_a4_5_can_execute_direct_historical_transition_without_automation(
    tmp_path: Path,
    target: ArtifactState,
) -> None:
    projects, handle = make_project(tmp_path)
    record = create_artifact(projects, handle.project_id)

    result = ArtifactLifecycleStore(projects).transition(
        handle.project_id,
        record.artifact_id,
        1,
        target,
    )
    assert result.current_state is target


@pytest.mark.parametrize(
    "terminal",
    [
        ArtifactState.REJECTED,
        ArtifactState.STALE,
        ArtifactState.SUPERSEDED,
    ],
)
def test_historical_states_are_terminal_in_persistence(
    tmp_path: Path,
    terminal: ArtifactState,
) -> None:
    projects, handle = make_project(tmp_path)
    record = create_artifact(projects, handle.project_id)
    lifecycle = ArtifactLifecycleStore(projects)

    lifecycle.transition(
        handle.project_id,
        record.artifact_id,
        1,
        terminal,
    )
    with pytest.raises(ArtifactLifecycleConflictError):
        lifecycle.transition(
            handle.project_id,
            record.artifact_id,
            1,
            ArtifactState.DRAFT,
        )


def test_transition_does_not_change_content_hash_or_creation_record(
    tmp_path: Path,
) -> None:
    projects, handle = make_project(tmp_path)
    record = create_artifact(projects, handle.project_id)
    original_hash = record.hash
    registry = handle.layout.project_dir / ARTIFACT_REGISTRY_DB_FILENAME

    with sqlite3.connect(registry) as connection:
        original_record_json = connection.execute(
            """
            SELECT record_json
            FROM artifact_versions
            WHERE artifact_id=? AND version=1
            """,
            (record.artifact_id.value,),
        ).fetchone()[0]

    lifecycle = ArtifactLifecycleStore(projects)
    lifecycle.transition(
        handle.project_id,
        record.artifact_id,
        1,
        ArtifactState.REVIEWED,
    )
    lifecycle.transition(
        handle.project_id,
        record.artifact_id,
        1,
        ArtifactState.VALIDATED,
        validation_result=pass_validation(),
    )

    reopened = ArtifactVersionStore(projects).open_version(
        handle.project_id,
        record.artifact_id,
        1,
    )
    assert reopened.hash == original_hash
    assert original_hash == compute_artifact_sha256(b"mass-budget-v1")

    with sqlite3.connect(registry) as connection:
        after_record_json = connection.execute(
            """
            SELECT record_json
            FROM artifact_versions
            WHERE artifact_id=? AND version=1
            """,
            (record.artifact_id.value,),
        ).fetchone()[0]

    assert after_record_json == original_record_json
    base = json.loads(after_record_json)
    assert base["status"] == "DRAFT"
    assert base["validation_result"] is None


def test_new_artifact_versions_always_start_draft(tmp_path: Path) -> None:
    projects, handle = make_project(tmp_path)
    versions = ArtifactVersionStore(projects)
    first = versions.create_artifact(
        handle.project_id,
        artifact_input(b"v1"),
    )

    lifecycle = ArtifactLifecycleStore(projects)
    lifecycle.transition(
        handle.project_id,
        first.artifact_id,
        1,
        ArtifactState.REVIEWED,
    )
    lifecycle.transition(
        handle.project_id,
        first.artifact_id,
        1,
        ArtifactState.VALIDATED,
        validation_result=pass_validation(),
    )
    lifecycle.transition(
        handle.project_id,
        first.artifact_id,
        1,
        ArtifactState.APPROVED,
    )

    second = versions.append_version(
        handle.project_id,
        first.artifact_id,
        ArtifactVersionInput(
            type="mass_budget",
            producer_task="TASK-000002",
            input_dependency_versions=(),
            content=b"v2",
            created_timestamp="2026-10-03T22:31:00Z",
        ),
    )

    assert second.status is ArtifactState.DRAFT
    assert second.validation_result is None
    assert versions.open_version(
        handle.project_id,
        first.artifact_id,
        1,
    ).status is ArtifactState.APPROVED


def test_corrupt_lifecycle_state_fails_closed(tmp_path: Path) -> None:
    projects, handle = make_project(tmp_path)
    record = create_artifact(projects, handle.project_id)
    registry = handle.layout.project_dir / ARTIFACT_REGISTRY_DB_FILENAME

    with sqlite3.connect(registry) as connection:
        connection.execute("PRAGMA ignore_check_constraints=ON")
        connection.execute(
            """
            UPDATE artifact_lifecycle
            SET status='INVALID'
            WHERE artifact_id=? AND version=1
            """,
            (record.artifact_id.value,),
        )
        connection.commit()

    with pytest.raises(Exception):
        ArtifactVersionStore(projects).open_version(
            handle.project_id,
            record.artifact_id,
            1,
        )


def test_validated_without_validation_fails_closed(tmp_path: Path) -> None:
    projects, handle = make_project(tmp_path)
    record = create_artifact(projects, handle.project_id)
    registry = handle.layout.project_dir / ARTIFACT_REGISTRY_DB_FILENAME

    with sqlite3.connect(registry) as connection:
        connection.execute(
            """
            UPDATE artifact_lifecycle
            SET status='VALIDATED',
                validation_result_json=NULL
            WHERE artifact_id=? AND version=1
            """,
            (record.artifact_id.value,),
        )
        connection.commit()

    with pytest.raises(Exception, match="requires PASS"):
        ArtifactVersionStore(projects).open_version(
            handle.project_id,
            record.artifact_id,
            1,
        )


def test_registry_schema_v4_has_lifecycle_and_supersession_and_phase3_schema_unchanged(
    tmp_path: Path,
) -> None:
    projects, handle = make_project(tmp_path)
    create_artifact(projects, handle.project_id)
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
        assert connection.execute(
            "PRAGMA user_version"
        ).fetchone()[0] == 4

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


def test_a4_3_v2_registry_migrates_lifecycle_to_v4(
    tmp_path: Path,
) -> None:
    projects, handle = make_project(tmp_path)
    registry = handle.layout.project_dir / ARTIFACT_REGISTRY_DB_FILENAME

    # Minimal clean A4.3 schema-v2 registry with one DRAFT record.
    record_json = json.dumps(
        {
            "artifact_id": "ART-000001",
            "project_id": handle.project_id.value,
            "version": 1,
            "type": "mass_budget",
            "producer_task": "TASK-000001",
            "input_dependency_versions": [],
            "hash": compute_artifact_sha256(b"legacy"),
            "status": "DRAFT",
            "validation_result": None,
            "created_timestamp": "2026-10-03T22:00:00Z",
        },
        sort_keys=True,
        separators=(",", ":"),
    ) + "\n"

    with sqlite3.connect(registry) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute(
            """
            CREATE TABLE artifact_registry_meta (
                singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
                schema_version INTEGER NOT NULL CHECK (schema_version = 2)
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
            )
            """
        )
        connection.execute(
            """
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
            )
            """
        )
        connection.execute(
            "INSERT INTO artifact_registry_meta VALUES (1, 2)"
        )
        connection.execute(
            """
            INSERT INTO artifact_versions(
                artifact_id, version, project_id,
                artifact_type, record_json
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (
                "ART-000001",
                1,
                handle.project_id.value,
                "mass_budget",
                record_json,
            ),
        )
        connection.execute(
            "INSERT INTO artifact_heads VALUES ('ART-000001', 1)"
        )
        connection.execute("PRAGMA user_version=2")
        connection.commit()

    # A4.5 initialization migrates schema and backfills lifecycle.
    from ai.artifacts.versions import initialize_artifact_registry
    initialize_artifact_registry(registry)

    with sqlite3.connect(registry) as connection:
        lifecycle = connection.execute(
            """
            SELECT status, validation_result_json
            FROM artifact_lifecycle
            WHERE artifact_id='ART-000001' AND version=1
            """
        ).fetchone()
        version = connection.execute(
            "PRAGMA user_version"
        ).fetchone()[0]

    assert lifecycle == ("DRAFT", None)
    assert version == 4


def test_lifecycle_store_has_no_propagation_or_supersede_policy_api(
    tmp_path: Path,
) -> None:
    projects, _handle = make_project(tmp_path)
    lifecycle = ArtifactLifecycleStore(projects)

    for name in (
        "propagate_stale",
        "mark_descendants_stale",
        "auto_supersede",
        "supersede_previous_versions",
        "approve_descendants",
    ):
        assert not hasattr(lifecycle, name)
