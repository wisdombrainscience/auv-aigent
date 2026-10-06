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
    ArtifactVersionInput,
    ArtifactVersionStore,
)
from ai.project.schema import PROJECT_SCHEMA_TABLES  # noqa: E402
from ai.project.store import ProjectStore  # noqa: E402


def main() -> int:
    with tempfile.TemporaryDirectory(
        prefix="remote-uav-artifact-versions-"
    ) as raw:
        projects = ProjectStore(
            Path(raw) / "remote-uav" / "projects"
        )
        handle = projects.create_project()
        store = ArtifactVersionStore(projects)

        first = store.create_artifact(
            handle.project_id,
            ArtifactVersionInput(
                type="mass_budget",
                producer_task="TASK-000001",
                input_dependency_versions=(),
                hash="a" * 64,
                created_timestamp="2026-10-03T18:30:00Z",
            ),
        )
        second = store.append_version(
            handle.project_id,
            first.artifact_id,
            ArtifactVersionInput(
                type="mass_budget",
                producer_task="TASK-000002",
                input_dependency_versions=(),
                hash="b" * 64,
                created_timestamp="2026-10-03T18:31:00Z",
            ),
        )

        restarted = ArtifactVersionStore(
            ProjectStore(projects.projects_root)
        )
        old = restarted.open_version(
            handle.project_id,
            first.artifact_id,
            1,
        )
        latest = restarted.open_latest(
            handle.project_id,
            first.artifact_id,
        )

        if old != first or latest != second:
            print("ARTIFACT_VERSIONS=FAIL reason=restart_mismatch")
            return 2

        registry = (
            handle.layout.project_dir
            / ARTIFACT_REGISTRY_DB_FILENAME
        )
        with sqlite3.connect(registry) as connection:
            registry_tables = tuple(
                row[0]
                for row in connection.execute(
                    """
                    SELECT name
                    FROM sqlite_master
                    WHERE type='table'
                      AND name NOT LIKE 'sqlite_%'
                    ORDER BY name
                    """
                )
            )
        with sqlite3.connect(projects.database_path) as connection:
            project_tables = tuple(
                row[0]
                for row in connection.execute(
                    """
                    SELECT name
                    FROM sqlite_master
                    WHERE type='table'
                      AND name NOT LIKE 'sqlite_%'
                    ORDER BY name
                    """
                )
            )

        if project_tables != PROJECT_SCHEMA_TABLES:
            print("ARTIFACT_VERSIONS=FAIL reason=project_schema_changed")
            return 3

        print(
            "ARTIFACT_VERSIONS=PASS "
            f"artifact={first.artifact_id.value} "
            "versions=2 "
            "latest=2 "
            "old_readable=PASS "
            "restart=PASS "
            f"registry_tables={len(registry_tables)} "
            f"project_schema_tables={len(project_tables)} "
            "edges=reserved_for_A4_3 "
            "hashing=reserved_for_A4_4"
        )
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
