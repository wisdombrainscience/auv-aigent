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
    ArtifactLifecycleStore,
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
from ai.project.schema import PROJECT_SCHEMA_TABLES  # noqa: E402
from ai.project.store import ProjectStore  # noqa: E402


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


def approve(projects, project_id, artifact_id, version):
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


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="remote-uav-superseded-") as raw:
        projects = ProjectStore(Path(raw) / "remote-uav" / "projects")
        handle = projects.create_project()
        versions = ArtifactVersionStore(projects)

        first = versions.create_artifact(
            handle.project_id,
            ArtifactVersionInput(
                type="mass_budget",
                producer_task="TASK-000001",
                input_dependency_versions=(),
                content=b"mass-budget-v1",
                created_timestamp="2026-10-04T03:00:00Z",
            ),
        )
        approve(projects, handle.project_id, first.artifact_id, 1)

        versions.append_version(
            handle.project_id,
            first.artifact_id,
            ArtifactVersionInput(
                type="mass_budget",
                producer_task="TASK-000002",
                input_dependency_versions=(),
                content=b"mass-budget-v2",
                created_timestamp="2026-10-04T03:01:00Z",
            ),
        )
        approve(projects, handle.project_id, first.artifact_id, 2)

        restarted = ProjectStore(projects.projects_root)
        reopened = ArtifactVersionStore(restarted)
        old = reopened.open_version(handle.project_id, first.artifact_id, 1)
        current = reopened.open_version(handle.project_id, first.artifact_id, 2)
        supersession = ArtifactSupersessionStore(restarted)
        replacement = supersession.replacement_for(handle.project_id, first.artifact_id, 1)
        current_ref = supersession.current_approved(handle.project_id, first.artifact_id)

        if old.status is not ArtifactState.SUPERSEDED:
            print("ARTIFACT_SUPERSEDED=FAIL reason=old_not_superseded")
            return 2
        if current.status is not ArtifactState.APPROVED:
            print("ARTIFACT_SUPERSEDED=FAIL reason=current_not_approved")
            return 3
        if replacement is None or replacement.version != 2:
            print("ARTIFACT_SUPERSEDED=FAIL reason=replacement_missing")
            return 4
        if current_ref is None or current_ref.version != 2:
            print("ARTIFACT_SUPERSEDED=FAIL reason=current_missing")
            return 5

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
        if project_tables != PROJECT_SCHEMA_TABLES:
            print("ARTIFACT_SUPERSEDED=FAIL reason=project_schema_changed")
            return 6

        print(
            "ARTIFACT_SUPERSEDED=PASS "
            f"artifact={first.artifact_id.value} "
            "old=v1:SUPERSEDED "
            "current=v2:APPROVED "
            "replacement=1->2 "
            "restart=PASS "
            f"registry_tables={len(registry_tables)} "
            f"project_schema_tables={len(project_tables)} "
            "graph_tests=reserved_for_A4_8"
        )
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
