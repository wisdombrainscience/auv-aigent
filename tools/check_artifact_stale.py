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
    ArtifactStaleStore,
    ArtifactVersionInput,
    ArtifactVersionStore,
)
from ai.contracts.artifact_states import ArtifactState  # noqa: E402
from ai.contracts.validation_result import (  # noqa: E402
    ValidationCheck,
    ValidationResult,
    ValidationStatus,
)
from ai.project.schema import PROJECT_SCHEMA_TABLES  # noqa: E402
from ai.project.store import ProjectStore  # noqa: E402


def pass_validation():
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


def approve(lifecycle, project_id, record):
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


def main() -> int:
    with tempfile.TemporaryDirectory(
        prefix="remote-uav-stale-propagation-"
    ) as raw:
        projects = ProjectStore(
            Path(raw) / "remote-uav" / "projects"
        )
        handle = projects.create_project()
        versions = ArtifactVersionStore(projects)
        lifecycle = ArtifactLifecycleStore(projects)

        mass = versions.create_artifact(
            handle.project_id,
            ArtifactVersionInput(
                type="mass_budget",
                producer_task="TASK-000001",
                input_dependency_versions=(),
                content=b"mass",
                created_timestamp="2026-10-04T01:00:00Z",
            ),
        )
        power = versions.create_artifact(
            handle.project_id,
            ArtifactVersionInput(
                type="power_budget",
                producer_task="TASK-000002",
                input_dependency_versions=(
                    ArtifactInputVersion(
                        entity_id=mass.artifact_id,
                        version=1,
                    ),
                ),
                content=b"power",
                created_timestamp="2026-10-04T01:01:00Z",
            ),
        )
        battery = versions.create_artifact(
            handle.project_id,
            ArtifactVersionInput(
                type="battery_selection",
                producer_task="TASK-000003",
                input_dependency_versions=(
                    ArtifactInputVersion(
                        entity_id=power.artifact_id,
                        version=1,
                    ),
                ),
                content=b"battery",
                created_timestamp="2026-10-04T01:02:00Z",
            ),
        )
        unrelated = versions.create_artifact(
            handle.project_id,
            ArtifactVersionInput(
                type="sensor_selection",
                producer_task="TASK-000004",
                input_dependency_versions=(),
                content=b"sensor",
                created_timestamp="2026-10-04T01:03:00Z",
            ),
        )

        for record in (mass, power, battery, unrelated):
            approve(lifecycle, handle.project_id, record)

        result = ArtifactStaleStore(projects).mark_artifact_stale(
            handle.project_id,
            mass.artifact_id,
            mass.version,
        )

        restarted = ArtifactVersionStore(
            ProjectStore(projects.projects_root)
        )
        if any(
            restarted.open_latest(
                handle.project_id,
                record.artifact_id,
            ).status is not ArtifactState.STALE
            for record in (mass, power, battery)
        ):
            print("ARTIFACT_STALE=FAIL reason=propagation")
            return 2
        if restarted.open_latest(
            handle.project_id,
            unrelated.artifact_id,
        ).status is not ArtifactState.APPROVED:
            print("ARTIFACT_STALE=FAIL reason=unrelated")
            return 3

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
            print("ARTIFACT_STALE=FAIL reason=project_schema")
            return 4

        print(
            "ARTIFACT_STALE=PASS "
            f"root={mass.artifact_id.value}@v1 "
            f"stale={len(result.changed_to_stale)} "
            "unrelated=APPROVED "
            "restart=PASS "
            "parent_gate=PASS "
            f"registry_tables={len(registry_tables)} "
            f"project_schema_tables={len(project_tables)} "
            "superseded=reserved_for_A4_7"
        )
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
