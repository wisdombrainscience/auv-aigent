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
    ArtifactVersionInput,
    ArtifactVersionStore,
)
from ai.contracts.artifact_states import ArtifactState  # noqa: E402
from ai.contracts.validation_result import (  # noqa: E402
    ValidationCheck,
    ValidationResult,
)
from ai.project.schema import PROJECT_SCHEMA_TABLES  # noqa: E402
from ai.project.store import ProjectStore  # noqa: E402


def pass_validation() -> ValidationResult:
    return ValidationResult.from_checks(
        (
            ValidationCheck.from_dict(
                {
                    "check_id": "probe-check",
                    "status": "PASS",
                    "field": None,
                    "reason": None,
                    "details": {"source": "deterministic-probe"},
                }
            ),
        )
    )


def main() -> int:
    with tempfile.TemporaryDirectory(
        prefix="remote-uav-artifact-states-"
    ) as raw:
        projects = ProjectStore(
            Path(raw) / "remote-uav" / "projects"
        )
        handle = projects.create_project()

        versions = ArtifactVersionStore(projects)
        record = versions.create_artifact(
            handle.project_id,
            ArtifactVersionInput(
                type="mass_budget",
                producer_task="TASK-000001",
                input_dependency_versions=(),
                content=b"mass-budget-state-probe",
                created_timestamp="2026-10-03T22:30:00Z",
            ),
        )
        original_hash = record.hash

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
        lifecycle.transition(
            handle.project_id,
            record.artifact_id,
            1,
            ArtifactState.APPROVED,
        )

        restarted = ProjectStore(projects.projects_root)
        reopened = ArtifactVersionStore(restarted).open_version(
            handle.project_id,
            record.artifact_id,
            1,
        )
        if reopened.status is not ArtifactState.APPROVED:
            print("ARTIFACT_STATES=FAIL reason=restart_state")
            return 2
        if reopened.validation_result != pass_validation():
            print("ARTIFACT_STATES=FAIL reason=validation")
            return 3
        if reopened.hash != original_hash:
            print("ARTIFACT_STATES=FAIL reason=hash_changed")
            return 4

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
            print("ARTIFACT_STATES=FAIL reason=project_schema_changed")
            return 5

        print(
            "ARTIFACT_STATES=PASS "
            f"artifact={record.artifact_id.value}@v1 "
            "path=DRAFT-REVIEWED-VALIDATED-APPROVED "
            "validation=PASS "
            "restart=PASS "
            "hash=stable "
            f"registry_tables={len(registry_tables)} "
            f"project_schema_tables={len(project_tables)} "
            "stale=manual "
            "superseded=manual"
        )
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
