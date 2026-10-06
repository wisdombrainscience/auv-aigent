from __future__ import annotations

from pathlib import Path
import sqlite3

import pytest

from ai.artifacts import (
    ARTIFACT_REGISTRY_DB_FILENAME,
    ArtifactContentStore,
    ArtifactInputVersion,
    ArtifactLifecycleConflictError,
    ArtifactLifecycleStore,
    ArtifactLineageStore,
    ArtifactStaleStore,
    ArtifactSupersessionStore,
    ArtifactVersionConflictError,
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


GRAPH_TEST_CHAIN_LENGTH = 32
GRAPH_TEST_BRANCH_WIDTH = 8


def make_project(tmp_path: Path):
    projects = ProjectStore(tmp_path / "remote-uav" / "projects")
    handle = projects.create_project()
    return projects, handle


def artifact_input(
    artifact_type: str,
    task_no: int,
    content: bytes,
    second: int,
    dependencies=(),
):
    return ArtifactVersionInput(
        type=artifact_type,
        producer_task=f"TASK-{task_no:06d}",
        input_dependency_versions=tuple(dependencies),
        content=content,
        created_timestamp=(
            f"2026-10-04T04:{second // 60:02d}:{second % 60:02d}Z"
        ),
    )


def pass_validation() -> ValidationResult:
    return ValidationResult.from_checks(
        (
            ValidationCheck(
                check_id="graph-deterministic",
                status=ValidationStatus.PASS,
                field=None,
                reason=None,
                details={},
            ),
        )
    )


def approve(projects, project_id, record):
    lifecycle = ArtifactLifecycleStore(projects)
    lifecycle.transition(
        project_id,
        record.artifact_id,
        record.version,
        ArtifactState.REVIEWED,
    )
    lifecycle.transition(
        project_id,
        record.artifact_id,
        record.version,
        ArtifactState.VALIDATED,
        validation_result=pass_validation(),
    )
    return lifecycle.transition(
        project_id,
        record.artifact_id,
        record.version,
        ArtifactState.APPROVED,
    )


def ref(record):
    return ArtifactInputVersion(
        entity_id=record.artifact_id,
        version=record.version,
    )


def test_graph_chain_lineage_is_exact_and_restart_stable(tmp_path: Path) -> None:
    projects, handle = make_project(tmp_path)
    versions = ArtifactVersionStore(projects)
    nodes = []
    for index in range(GRAPH_TEST_CHAIN_LENGTH):
        deps = () if not nodes else (ref(nodes[-1]),)
        nodes.append(
            versions.create_artifact(
                handle.project_id,
                artifact_input(
                    f"chain_{index}",
                    index + 1,
                    f"chain-{index}".encode(),
                    index,
                    dependencies=deps,
                ),
            )
        )

    lineage = ArtifactLineageStore(projects)
    lineage.verify_project(handle.project_id)
    for index, node in enumerate(nodes):
        expected = () if index == 0 else (ref(nodes[index - 1]),)
        assert lineage.dependencies(
            handle.project_id,
            node.artifact_id,
            1,
        ) == expected

    restarted = ArtifactLineageStore(
        ProjectStore(projects.projects_root)
    )
    restarted.verify_project(handle.project_id)
    assert restarted.dependencies(
        handle.project_id,
        nodes[-1].artifact_id,
        1,
    ) == (ref(nodes[-2]),)


def test_diamond_stale_propagation_visits_join_once(tmp_path: Path) -> None:
    projects, handle = make_project(tmp_path)
    versions = ArtifactVersionStore(projects)
    root = versions.create_artifact(
        handle.project_id,
        artifact_input("root", 1, b"root", 0),
    )
    left = versions.create_artifact(
        handle.project_id,
        artifact_input("left", 2, b"left", 1, (ref(root),)),
    )
    right = versions.create_artifact(
        handle.project_id,
        artifact_input("right", 3, b"right", 2, (ref(root),)),
    )
    join = versions.create_artifact(
        handle.project_id,
        artifact_input(
            "join",
            4,
            b"join",
            3,
            (ref(left), ref(right)),
        ),
    )
    unrelated = versions.create_artifact(
        handle.project_id,
        artifact_input("unrelated", 5, b"u", 4),
    )
    for record in (root, left, right, join, unrelated):
        approve(projects, handle.project_id, record)

    result = ArtifactStaleStore(projects).mark_artifact_stale(
        handle.project_id,
        root.artifact_id,
        1,
    )
    changed = [
        (item.artifact_id.value, item.version)
        for item in result.changed_to_stale
    ]
    assert len(changed) == 4
    assert len(set(changed)) == 4
    assert versions.open_latest(
        handle.project_id,
        join.artifact_id,
    ).status is ArtifactState.STALE
    assert versions.open_latest(
        handle.project_id,
        unrelated.artifact_id,
    ).status is ArtifactState.APPROVED


def test_requirement_invalidation_fans_out_and_reconverges(tmp_path: Path) -> None:
    projects, handle = make_project(tmp_path)
    reqs = RequirementVersionStore(projects)
    v1 = reqs.create_initial(handle.project_id, ["Payload <= 2 kg"])
    requirement_id = v1.requirement_set.requirements[0].requirement_id
    v2 = reqs.append_version(
        handle.project_id,
        [
            RequirementVersionInput(
                requirement_id=requirement_id,
                text="Payload <= 2.2 kg",
            )
        ],
    )
    dependency = ArtifactInputVersion(
        entity_id=requirement_id,
        version=v2.version,
    )
    versions = ArtifactVersionStore(projects)
    first = versions.create_artifact(
        handle.project_id,
        artifact_input("a", 1, b"a", 0, (dependency,)),
    )
    second = versions.create_artifact(
        handle.project_id,
        artifact_input("b", 2, b"b", 1, (dependency,)),
    )
    joined = versions.create_artifact(
        handle.project_id,
        artifact_input(
            "c",
            3,
            b"c",
            2,
            (ref(first), ref(second)),
        ),
    )
    for record in (first, second, joined):
        approve(projects, handle.project_id, record)

    result = ArtifactStaleStore(
        projects
    ).propagate_dependency_invalidation(
        handle.project_id,
        dependency,
    )
    assert len(result.changed_to_stale) == 3
    assert all(
        versions.open_latest(
            handle.project_id,
            record.artifact_id,
        ).status
        is ArtifactState.STALE
        for record in (first, second, joined)
    )


def test_version_specific_edges_do_not_alias_versions(tmp_path: Path) -> None:
    projects, handle = make_project(tmp_path)
    versions = ArtifactVersionStore(projects)
    source_v1 = versions.create_artifact(
        handle.project_id,
        artifact_input("source", 1, b"v1", 0),
    )
    source_v2 = versions.append_version(
        handle.project_id,
        source_v1.artifact_id,
        artifact_input("source", 2, b"v2", 1),
    )
    child_v1 = versions.create_artifact(
        handle.project_id,
        artifact_input("child_v1", 3, b"c1", 2, (ref(source_v1),)),
    )
    child_v2 = versions.create_artifact(
        handle.project_id,
        artifact_input("child_v2", 4, b"c2", 3, (ref(source_v2),)),
    )
    lineage = ArtifactLineageStore(projects)
    dependents_v1 = lineage.direct_dependents(
        handle.project_id,
        ref(source_v1),
    )
    dependents_v2 = lineage.direct_dependents(
        handle.project_id,
        ref(source_v2),
    )
    assert [
        (item.artifact_id.value, item.version)
        for item in dependents_v1
    ] == [(child_v1.artifact_id.value, 1)]
    assert [
        (item.artifact_id.value, item.version)
        for item in dependents_v2
    ] == [(child_v2.artifact_id.value, 1)]


def test_supersession_keeps_historical_edge_then_explicit_invalidation_stales_child(
    tmp_path: Path,
) -> None:
    projects, handle = make_project(tmp_path)
    versions = ArtifactVersionStore(projects)
    parent_v1 = versions.create_artifact(
        handle.project_id,
        artifact_input("parent", 1, b"p1", 0),
    )
    approve(projects, handle.project_id, parent_v1)
    child = versions.create_artifact(
        handle.project_id,
        artifact_input("child", 2, b"child", 1, (ref(parent_v1),)),
    )
    approve(projects, handle.project_id, child)
    parent_v2 = versions.append_version(
        handle.project_id,
        parent_v1.artifact_id,
        artifact_input("parent", 3, b"p2", 2, (ref(parent_v1),)),
    )
    approve(projects, handle.project_id, parent_v2)

    assert versions.open_version(
        handle.project_id,
        parent_v1.artifact_id,
        1,
    ).status is ArtifactState.SUPERSEDED
    assert versions.open_version(
        handle.project_id,
        child.artifact_id,
        1,
    ).status is ArtifactState.APPROVED
    assert ArtifactLineageStore(projects).dependencies(
        handle.project_id,
        child.artifact_id,
        1,
    ) == (ref(parent_v1),)

    result = ArtifactStaleStore(
        projects
    ).propagate_dependency_invalidation(
        handle.project_id,
        ref(parent_v1),
    )
    assert (child.artifact_id.value, 1) in {
        (item.artifact_id.value, item.version)
        for item in result.changed_to_stale
    }
    assert versions.open_version(
        handle.project_id,
        child.artifact_id,
        1,
    ).status is ArtifactState.STALE


def test_current_approved_tracks_latest_approved_not_latest_draft(tmp_path: Path) -> None:
    projects, handle = make_project(tmp_path)
    versions = ArtifactVersionStore(projects)
    first = versions.create_artifact(
        handle.project_id,
        artifact_input("design", 1, b"v1", 0),
    )
    approve(projects, handle.project_id, first)
    second = versions.append_version(
        handle.project_id,
        first.artifact_id,
        artifact_input("design", 2, b"v2", 1),
    )
    current = ArtifactSupersessionStore(projects).current_approved(
        handle.project_id,
        first.artifact_id,
    )
    assert current is not None and current.version == 1
    assert versions.open_version(
        handle.project_id,
        second.artifact_id,
        2,
    ).status is ArtifactState.DRAFT


def test_approving_new_version_creates_linear_replacement_chain(tmp_path: Path) -> None:
    projects, handle = make_project(tmp_path)
    versions = ArtifactVersionStore(projects)
    first = versions.create_artifact(
        handle.project_id,
        artifact_input("design", 1, b"v1", 0),
    )
    approve(projects, handle.project_id, first)
    second = versions.append_version(
        handle.project_id,
        first.artifact_id,
        artifact_input("design", 2, b"v2", 1),
    )
    approve(projects, handle.project_id, second)
    third = versions.append_version(
        handle.project_id,
        first.artifact_id,
        artifact_input("design", 3, b"v3", 2),
    )
    approve(projects, handle.project_id, third)
    supersession = ArtifactSupersessionStore(projects)
    assert supersession.replacement_for(
        handle.project_id,
        first.artifact_id,
        1,
    ).version == 2
    assert supersession.replacement_for(
        handle.project_id,
        first.artifact_id,
        2,
    ).version == 3
    assert supersession.current_approved(
        handle.project_id,
        first.artifact_id,
    ).version == 3


def test_forward_and_self_current_edges_are_rejected_deterministically(
    tmp_path: Path,
) -> None:
    projects, handle = make_project(tmp_path)
    versions = ArtifactVersionStore(projects)
    first = versions.create_artifact(
        handle.project_id,
        artifact_input("x", 1, b"x1", 0),
    )
    with pytest.raises(ArtifactVersionConflictError, match="does not exist"):
        versions.append_version(
            handle.project_id,
            first.artifact_id,
            artifact_input(
                "x",
                2,
                b"x2",
                1,
                (
                    ArtifactInputVersion(
                        entity_id=first.artifact_id,
                        version=2,
                    ),
                ),
            ),
        )
    with pytest.raises(ArtifactVersionConflictError, match="does not exist"):
        versions.create_artifact(
            handle.project_id,
            artifact_input(
                "future",
                3,
                b"f",
                2,
                (
                    ArtifactInputVersion(
                        entity_id="ART-999999",
                        version=1,
                    ),
                ),
            ),
        )


def test_cross_project_artifact_edges_are_rejected(tmp_path: Path) -> None:
    projects = ProjectStore(tmp_path / "remote-uav" / "projects")
    first_project = projects.create_project()
    second_project = projects.create_project()
    versions = ArtifactVersionStore(projects)
    source = versions.create_artifact(
        first_project.project_id,
        artifact_input("source", 1, b"s", 0),
    )
    with pytest.raises(ArtifactVersionConflictError, match="does not exist"):
        versions.create_artifact(
            second_project.project_id,
            artifact_input("child", 2, b"c", 1, (ref(source),)),
        )


def test_stale_required_parent_blocks_approval(tmp_path: Path) -> None:
    projects, handle = make_project(tmp_path)
    versions = ArtifactVersionStore(projects)
    parent = versions.create_artifact(
        handle.project_id,
        artifact_input("parent", 1, b"p", 0),
    )
    child = versions.create_artifact(
        handle.project_id,
        artifact_input("child", 2, b"c", 1, (ref(parent),)),
    )
    lifecycle = ArtifactLifecycleStore(projects)
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
    lifecycle.transition(
        handle.project_id,
        parent.artifact_id,
        1,
        ArtifactState.STALE,
    )
    with pytest.raises(ArtifactLifecycleConflictError, match="STALE"):
        lifecycle.transition(
            handle.project_id,
            child.artifact_id,
            1,
            ArtifactState.APPROVED,
        )


def test_edge_tamper_fails_registry_verification(tmp_path: Path) -> None:
    projects, handle = make_project(tmp_path)
    versions = ArtifactVersionStore(projects)
    parent = versions.create_artifact(
        handle.project_id,
        artifact_input("parent", 1, b"p", 0),
    )
    child = versions.create_artifact(
        handle.project_id,
        artifact_input("child", 2, b"c", 1, (ref(parent),)),
    )
    registry = handle.layout.project_dir / ARTIFACT_REGISTRY_DB_FILENAME
    with sqlite3.connect(registry) as connection:
        connection.execute(
            """
            UPDATE artifact_dependency_edges
            SET dependency_version=2
            WHERE child_artifact_id=? AND child_version=1
            """,
            (child.artifact_id.value,),
        )
        connection.commit()
    with pytest.raises(Exception, match="do not match immutable record"):
        ArtifactLineageStore(projects).verify_project(handle.project_id)


def test_fanout_graph_direct_dependents_is_complete(tmp_path: Path) -> None:
    projects, handle = make_project(tmp_path)
    versions = ArtifactVersionStore(projects)
    root = versions.create_artifact(
        handle.project_id,
        artifact_input("root", 1, b"r", 0),
    )
    children = []
    for index in range(GRAPH_TEST_BRANCH_WIDTH):
        children.append(
            versions.create_artifact(
                handle.project_id,
                artifact_input(
                    f"child_{index}",
                    index + 2,
                    f"c{index}".encode(),
                    index + 1,
                    (ref(root),),
                ),
            )
        )
    dependents = ArtifactLineageStore(projects).direct_dependents(
        handle.project_id,
        ref(root),
    )
    assert len(dependents) == GRAPH_TEST_BRANCH_WIDTH
    assert {
        item.artifact_id.value
        for item in dependents
    } == {
        item.artifact_id.value
        for item in children
    }


def test_hashes_and_payloads_survive_lineage_and_lifecycle_operations(
    tmp_path: Path,
) -> None:
    projects, handle = make_project(tmp_path)
    versions = ArtifactVersionStore(projects)
    parent = versions.create_artifact(
        handle.project_id,
        artifact_input("parent", 1, b"parent-payload", 0),
    )
    child = versions.create_artifact(
        handle.project_id,
        artifact_input(
            "child",
            2,
            b"child-payload",
            1,
            (ref(parent),),
        ),
    )
    hashes = (parent.hash, child.hash)
    approve(projects, handle.project_id, parent)
    approve(projects, handle.project_id, child)
    ArtifactStaleStore(projects).mark_artifact_stale(
        handle.project_id,
        parent.artifact_id,
        1,
    )
    assert versions.open_version(
        handle.project_id,
        parent.artifact_id,
        1,
    ).hash == hashes[0]
    assert versions.open_version(
        handle.project_id,
        child.artifact_id,
        1,
    ).hash == hashes[1]
    content = ArtifactContentStore(projects)
    assert content.read(handle.project_id, hashes[0]) == b"parent-payload"
    assert content.read(handle.project_id, hashes[1]) == b"child-payload"


def test_registry_and_project_schema_remain_stable_after_graph_workload(
    tmp_path: Path,
) -> None:
    projects, handle = make_project(tmp_path)
    versions = ArtifactVersionStore(projects)
    root = versions.create_artifact(
        handle.project_id,
        artifact_input("root", 1, b"r", 0),
    )
    versions.create_artifact(
        handle.project_id,
        artifact_input("child", 2, b"c", 1, (ref(root),)),
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
