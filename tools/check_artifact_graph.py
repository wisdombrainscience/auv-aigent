#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
import sqlite3
import sys
import tempfile

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from ai.artifacts import (  # noqa: E402
    ARTIFACT_REGISTRY_DB_FILENAME,
    ArtifactInputVersion,
    ArtifactLifecycleStore,
    ArtifactLineageStore,
    ArtifactStaleStore,
    ArtifactSupersessionStore,
    ArtifactVersionInput,
    ArtifactVersionStore,
)
from ai.contracts.artifact_states import ArtifactState  # noqa: E402
from ai.contracts.validation_result import (  # noqa: E402
    ValidationCheck,
    ValidationResult,
    ValidationStatus,
)
from ai.project.requirement_versions import (  # noqa: E402
    RequirementVersionInput,
    RequirementVersionStore,
)
from ai.project.schema import PROJECT_SCHEMA_TABLES  # noqa: E402
from ai.project.store import ProjectStore  # noqa: E402


def pass_validation():
    return ValidationResult.from_checks(
        (
            ValidationCheck(
                check_id="graph",
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
    lifecycle.transition(
        project_id,
        record.artifact_id,
        record.version,
        ArtifactState.APPROVED,
    )


def artifact_input(kind, task, content, second, dependencies=()):
    return ArtifactVersionInput(
        type=kind,
        producer_task=f"TASK-{task:06d}",
        input_dependency_versions=tuple(dependencies),
        content=content,
        created_timestamp=f"2026-10-04T05:00:{second:02d}Z",
    )


def ref(record):
    return ArtifactInputVersion(
        entity_id=record.artifact_id,
        version=record.version,
    )


def main() -> int:
    with tempfile.TemporaryDirectory(
        prefix="remote-uav-artifact-graph-"
    ) as raw:
        projects = ProjectStore(
            Path(raw) / "remote-uav" / "projects"
        )
        handle = projects.create_project()

        requirements = RequirementVersionStore(projects)
        req_v1 = requirements.create_initial(
            handle.project_id,
            ["Payload <= 2 kg"],
        )
        requirement_id = (
            req_v1.requirement_set.requirements[0].requirement_id
        )
        req_v2 = requirements.append_version(
            handle.project_id,
            [
                RequirementVersionInput(
                    requirement_id=requirement_id,
                    text="Payload <= 2.2 kg",
                )
            ],
        )
        requirement_dependency = ArtifactInputVersion(
            entity_id=requirement_id,
            version=req_v2.version,
        )

        versions = ArtifactVersionStore(projects)
        root = versions.create_artifact(
            handle.project_id,
            artifact_input(
                "root",
                1,
                b"root",
                0,
                (requirement_dependency,),
            ),
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

        ArtifactLineageStore(projects).verify_project(
            handle.project_id
        )
        stale_result = ArtifactStaleStore(
            projects
        ).propagate_dependency_invalidation(
            handle.project_id,
            requirement_dependency,
        )
        if len(stale_result.changed_to_stale) != 4:
            print("ARTIFACT_GRAPH=FAIL reason=stale_count")
            return 2
        if versions.open_latest(
            handle.project_id,
            unrelated.artifact_id,
        ).status is not ArtifactState.APPROVED:
            print("ARTIFACT_GRAPH=FAIL reason=unrelated")
            return 3

        root_v2 = versions.append_version(
            handle.project_id,
            root.artifact_id,
            artifact_input(
                "root",
                6,
                b"root-v2",
                5,
                (),
            ),
        )
        approve(projects, handle.project_id, root_v2)
        current = ArtifactSupersessionStore(
            projects
        ).current_approved(
            handle.project_id,
            root.artifact_id,
        )
        if current is None or current.version != 2:
            print("ARTIFACT_GRAPH=FAIL reason=current")
            return 4

        restarted = ProjectStore(projects.projects_root)
        ArtifactLineageStore(restarted).verify_project(
            handle.project_id
        )
        current_after_restart = ArtifactSupersessionStore(
            restarted
        ).current_approved(
            handle.project_id,
            root.artifact_id,
        )
        if (
            current_after_restart is None
            or current_after_restart.version != 2
        ):
            print("ARTIFACT_GRAPH=FAIL reason=restart")
            return 5

        registry = (
            handle.layout.project_dir
            / ARTIFACT_REGISTRY_DB_FILENAME
        )
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
        if project_tables != PROJECT_SCHEMA_TABLES:
            print("ARTIFACT_GRAPH=FAIL reason=project_schema")
            return 6

        print(
            "ARTIFACT_GRAPH=PASS "
            "nodes=6 "
            "diamond=PASS "
            "requirement_edges=PASS "
            "versioned_edges=PASS "
            "stale=4 "
            "unrelated=APPROVED "
            "current=v2 "
            "restart=PASS "
            f"registry_tables={len(registry_tables)} "
            f"project_schema_tables={len(project_tables)} "
            "cp4=READY"
        )
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
