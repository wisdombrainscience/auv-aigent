from __future__ import annotations

from pathlib import Path
import sqlite3

import pytest

from ai.artifacts import (
    ARTIFACT_REGISTRY_DB_FILENAME,
    ARTIFACT_STALE_PROPAGATION_CONTRACT_VERSION,
    MAX_STALE_PROPAGATION_ARTIFACTS,
    ArtifactInputVersion,
    ArtifactLifecycleConflictError,
    ArtifactLifecycleStore,
    ArtifactStaleNotFoundError,
    ArtifactStaleStore,
    ArtifactVersionInput,
    ArtifactVersionStore,
)
from ai.contracts.artifact_states import ArtifactState
from ai.contracts.validation_result import (
    ValidationCheck,
    ValidationResult,
    ValidationStatus,
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
    return projects, handle


def artifact_input(
    artifact_type: str,
    task: str,
    content: bytes,
    timestamp: str,
    dependencies=(),
):
    return ArtifactVersionInput(
        type=artifact_type,
        producer_task=task,
        input_dependency_versions=tuple(dependencies),
        content=content,
        created_timestamp=timestamp,
    )


def pass_validation() -> ValidationResult:
    return ValidationResult.from_checks(
        (
            ValidationCheck(
                check_id="deterministic",
                status=ValidationStatus.PASS,
                field=None,
                reason=None,
                details={},
            ),
        )
    )


def approve(
    lifecycle: ArtifactLifecycleStore,
    project_id,
    artifact_id,
    version: int,
):
    lifecycle.transition(
        project_id,
        artifact_id,
        version,
        ArtifactState.REVIEWED,
    )
    lifecycle.transition(
        project_id,
        artifact_id,
        version,
        ArtifactState.VALIDATED,
        validation_result=pass_validation(),
    )
    return lifecycle.transition(
        project_id,
        artifact_id,
        version,
        ArtifactState.APPROVED,
    )


def build_chain(tmp_path: Path):
    projects, handle = make_project(tmp_path)
    versions = ArtifactVersionStore(projects)
    lifecycle = ArtifactLifecycleStore(projects)

    mass = versions.create_artifact(
        handle.project_id,
        artifact_input(
            "mass_budget",
            "TASK-000001",
            b"mass",
            "2026-10-04T01:00:00Z",
        ),
    )
    power = versions.create_artifact(
        handle.project_id,
        artifact_input(
            "power_budget",
            "TASK-000002",
            b"power",
            "2026-10-04T01:01:00Z",
            dependencies=(
                ArtifactInputVersion(
                    entity_id=mass.artifact_id,
                    version=mass.version,
                ),
            ),
        ),
    )
    battery = versions.create_artifact(
        handle.project_id,
        artifact_input(
            "battery_selection",
            "TASK-000003",
            b"battery",
            "2026-10-04T01:02:00Z",
            dependencies=(
                ArtifactInputVersion(
                    entity_id=power.artifact_id,
                    version=power.version,
                ),
            ),
        ),
    )
    unrelated = versions.create_artifact(
        handle.project_id,
        artifact_input(
            "sensor_selection",
            "TASK-000004",
            b"sensor",
            "2026-10-04T01:03:00Z",
        ),
    )

    for record in (mass, power, battery, unrelated):
        approve(
            lifecycle,
            handle.project_id,
            record.artifact_id,
            record.version,
        )

    return (
        projects,
        handle,
        versions,
        lifecycle,
        mass,
        power,
        battery,
        unrelated,
    )


def test_contract_and_bound_are_explicit() -> None:
    assert ARTIFACT_STALE_PROPAGATION_CONTRACT_VERSION == "1.0"
    assert MAX_STALE_PROPAGATION_ARTIFACTS == 4096


def test_mark_root_stale_propagates_transitively_and_leaves_unrelated(
    tmp_path: Path,
) -> None:
    (
        projects,
        handle,
        versions,
        _lifecycle,
        mass,
        power,
        battery,
        unrelated,
    ) = build_chain(tmp_path)

    result = ArtifactStaleStore(projects).mark_artifact_stale(
        handle.project_id,
        mass.artifact_id,
        mass.version,
    )

    assert [
        (item.artifact_id.value, item.version)
        for item in result.changed_to_stale
    ] == [
        (mass.artifact_id.value, 1),
        (power.artifact_id.value, 1),
        (battery.artifact_id.value, 1),
    ]

    assert versions.open_latest(
        handle.project_id,
        mass.artifact_id,
    ).status is ArtifactState.STALE
    assert versions.open_latest(
        handle.project_id,
        power.artifact_id,
    ).status is ArtifactState.STALE
    assert versions.open_latest(
        handle.project_id,
        battery.artifact_id,
    ).status is ArtifactState.STALE
    assert versions.open_latest(
        handle.project_id,
        unrelated.artifact_id,
    ).status is ArtifactState.APPROVED


def test_requirement_dependency_invalidation_marks_direct_and_descendants(
    tmp_path: Path,
) -> None:
    projects, handle = make_project(tmp_path)
    requirements = RequirementVersionStore(projects)
    v1 = requirements.create_initial(
        handle.project_id,
        ["Payload shall be <= 2 kg."],
    )
    req_id = v1.requirement_set.requirements[0].requirement_id
    v2 = requirements.append_version(
        handle.project_id,
        [
            RequirementVersionInput(
                requirement_id=req_id,
                text="Payload shall be <= 2.2 kg.",
            )
        ],
    )

    versions = ArtifactVersionStore(projects)
    lifecycle = ArtifactLifecycleStore(projects)
    mass = versions.create_artifact(
        handle.project_id,
        artifact_input(
            "mass_budget",
            "TASK-000001",
            b"mass",
            "2026-10-04T01:00:00Z",
            dependencies=(
                ArtifactInputVersion(
                    entity_id=req_id,
                    version=v2.version,
                ),
            ),
        ),
    )
    power = versions.create_artifact(
        handle.project_id,
        artifact_input(
            "power_budget",
            "TASK-000002",
            b"power",
            "2026-10-04T01:01:00Z",
            dependencies=(
                ArtifactInputVersion(
                    entity_id=mass.artifact_id,
                    version=1,
                ),
            ),
        ),
    )
    approve(lifecycle, handle.project_id, mass.artifact_id, 1)
    approve(lifecycle, handle.project_id, power.artifact_id, 1)

    result = ArtifactStaleStore(
        projects
    ).propagate_dependency_invalidation(
        handle.project_id,
        ArtifactInputVersion(
            entity_id=req_id,
            version=v2.version,
        ),
    )

    assert len(result.changed_to_stale) == 2
    assert versions.open_latest(
        handle.project_id,
        mass.artifact_id,
    ).status is ArtifactState.STALE
    assert versions.open_latest(
        handle.project_id,
        power.artifact_id,
    ).status is ArtifactState.STALE


def test_rejected_historical_node_is_not_rewritten_but_descendants_traverse(
    tmp_path: Path,
) -> None:
    projects, handle = make_project(tmp_path)
    versions = ArtifactVersionStore(projects)
    lifecycle = ArtifactLifecycleStore(projects)

    root = versions.create_artifact(
        handle.project_id,
        artifact_input(
            "root",
            "TASK-000001",
            b"root",
            "2026-10-04T01:00:00Z",
        ),
    )
    mid = versions.create_artifact(
        handle.project_id,
        artifact_input(
            "mid",
            "TASK-000002",
            b"mid",
            "2026-10-04T01:01:00Z",
            dependencies=(
                ArtifactInputVersion(
                    entity_id=root.artifact_id,
                    version=1,
                ),
            ),
        ),
    )
    leaf = versions.create_artifact(
        handle.project_id,
        artifact_input(
            "leaf",
            "TASK-000003",
            b"leaf",
            "2026-10-04T01:02:00Z",
            dependencies=(
                ArtifactInputVersion(
                    entity_id=mid.artifact_id,
                    version=1,
                ),
            ),
        ),
    )

    lifecycle.transition(
        handle.project_id,
        mid.artifact_id,
        1,
        ArtifactState.REJECTED,
    )
    approve(lifecycle, handle.project_id, leaf.artifact_id, 1)

    result = ArtifactStaleStore(projects).mark_artifact_stale(
        handle.project_id,
        root.artifact_id,
        1,
    )

    assert mid.artifact_id.value in {
        item.artifact_id.value for item in result.already_historical
    }
    assert versions.open_latest(
        handle.project_id,
        mid.artifact_id,
    ).status is ArtifactState.REJECTED
    assert versions.open_latest(
        handle.project_id,
        leaf.artifact_id,
    ).status is ArtifactState.STALE


def test_already_stale_operation_is_idempotent_at_state_level(
    tmp_path: Path,
) -> None:
    (
        projects,
        handle,
        versions,
        _lifecycle,
        mass,
        power,
        battery,
        _unrelated,
    ) = build_chain(tmp_path)
    stale = ArtifactStaleStore(projects)

    first = stale.mark_artifact_stale(
        handle.project_id,
        mass.artifact_id,
        1,
    )
    second = stale.mark_artifact_stale(
        handle.project_id,
        mass.artifact_id,
        1,
    )

    assert len(first.changed_to_stale) == 3
    assert len(second.changed_to_stale) == 0
    assert len(second.already_historical) == 3
    assert versions.open_latest(
        handle.project_id,
        battery.artifact_id,
    ).status is ArtifactState.STALE


def test_missing_root_artifact_fails_before_mutation(tmp_path: Path) -> None:
    projects, handle = make_project(tmp_path)

    with pytest.raises(ArtifactStaleNotFoundError):
        ArtifactStaleStore(projects).mark_artifact_stale(
            handle.project_id,
            "ART-999999",
            1,
        )


def test_approved_transition_fails_when_required_parent_is_stale(
    tmp_path: Path,
) -> None:
    projects, handle = make_project(tmp_path)
    versions = ArtifactVersionStore(projects)
    lifecycle = ArtifactLifecycleStore(projects)

    parent = versions.create_artifact(
        handle.project_id,
        artifact_input(
            "parent",
            "TASK-000001",
            b"parent",
            "2026-10-04T01:00:00Z",
        ),
    )
    child = versions.create_artifact(
        handle.project_id,
        artifact_input(
            "child",
            "TASK-000002",
            b"child",
            "2026-10-04T01:01:00Z",
            dependencies=(
                ArtifactInputVersion(
                    entity_id=parent.artifact_id,
                    version=1,
                ),
            ),
        ),
    )

    # Use the A4.5 primitive to create a stale direct parent without invoking
    # A4.6 propagation first; this isolates the APPROVED currentness gate.
    lifecycle.transition(
        handle.project_id,
        parent.artifact_id,
        1,
        ArtifactState.STALE,
    )

    lifecycle.transition(
        handle.project_id,
        child.artifact_id,
        1,
        ArtifactState.REVIEWED,
    )
    lifecycle.transition(
        handle.project_id,
        child.artifact_id,
        1,
        ArtifactState.VALIDATED,
        validation_result=pass_validation(),
    )

    with pytest.raises(
        ArtifactLifecycleConflictError,
        match="required artifact dependency is STALE",
    ):
        lifecycle.transition(
            handle.project_id,
            child.artifact_id,
            1,
            ArtifactState.APPROVED,
        )


def test_restart_preserves_propagated_stale_state(tmp_path: Path) -> None:
    (
        projects,
        handle,
        _versions,
        _lifecycle,
        mass,
        _power,
        battery,
        _unrelated,
    ) = build_chain(tmp_path)

    ArtifactStaleStore(projects).mark_artifact_stale(
        handle.project_id,
        mass.artifact_id,
        1,
    )

    restarted = ArtifactVersionStore(
        ProjectStore(projects.projects_root)
    )
    assert restarted.open_latest(
        handle.project_id,
        battery.artifact_id,
    ).status is ArtifactState.STALE


def test_propagation_does_not_change_hash_or_content(
    tmp_path: Path,
) -> None:
    (
        projects,
        handle,
        versions,
        _lifecycle,
        mass,
        _power,
        _battery,
        _unrelated,
    ) = build_chain(tmp_path)

    before = versions.open_latest(
        handle.project_id,
        mass.artifact_id,
    )
    ArtifactStaleStore(projects).mark_artifact_stale(
        handle.project_id,
        mass.artifact_id,
        1,
    )
    after = versions.open_latest(
        handle.project_id,
        mass.artifact_id,
    )

    assert after.hash == before.hash
    assert after.created_timestamp == before.created_timestamp
    assert after.input_dependency_versions == before.input_dependency_versions


def test_registry_and_phase3_schema_are_unchanged(tmp_path: Path) -> None:
    projects, handle = make_project(tmp_path)
    versions = ArtifactVersionStore(projects)
    root = versions.create_artifact(
        handle.project_id,
        artifact_input(
            "root",
            "TASK-000001",
            b"root",
            "2026-10-04T01:00:00Z",
        ),
    )
    ArtifactStaleStore(projects).mark_artifact_stale(
        handle.project_id,
        root.artifact_id,
        1,
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

    assert len(registry_tables) == 6
    assert project_tables == PROJECT_SCHEMA_TABLES


def test_stale_store_has_no_superseded_policy_api(tmp_path: Path) -> None:
    projects, _handle = make_project(tmp_path)
    stale = ArtifactStaleStore(projects)

    for name in (
        "supersede",
        "mark_superseded",
        "replace_version",
        "approve",
        "compute_hash",
    ):
        assert not hasattr(stale, name)
