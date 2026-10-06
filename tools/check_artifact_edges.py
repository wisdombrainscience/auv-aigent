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
    ArtifactLineageStore,
    ArtifactVersionInput,
    ArtifactVersionStore,
)
from ai.project.requirement_versions import (  # noqa: E402
    RequirementVersionInput,
    RequirementVersionStore,
)
from ai.project.schema import PROJECT_SCHEMA_TABLES  # noqa: E402
from ai.project.store import ProjectStore  # noqa: E402


def main() -> int:
    with tempfile.TemporaryDirectory(
        prefix="remote-uav-artifact-lineage-"
    ) as raw:
        projects = ProjectStore(
            Path(raw) / "remote-uav" / "projects"
        )
        handle = projects.create_project()

        requirements = RequirementVersionStore(projects)
        req_v1 = requirements.create_initial(
            handle.project_id,
            ["Payload shall not exceed 2 kg."],
        )
        req_id = req_v1.requirement_set.requirements[0].requirement_id
        req_v2 = requirements.append_version(
            handle.project_id,
            [
                RequirementVersionInput(
                    requirement_id=req_id,
                    text="Payload shall not exceed 2.2 kg.",
                ),
            ],
        )

        versions = ArtifactVersionStore(projects)
        source = versions.create_artifact(
            handle.project_id,
            ArtifactVersionInput(
                type="requirements_summary",
                producer_task="TASK-000001",
                input_dependency_versions=(
                    ArtifactInputVersion(
                        entity_id=req_id,
                        version=req_v2.version,
                    ),
                ),
                hash="a" * 64,
                created_timestamp="2026-10-03T19:30:00Z",
            ),
        )
        child = versions.create_artifact(
            handle.project_id,
            ArtifactVersionInput(
                type="mass_budget",
                producer_task="TASK-000002",
                input_dependency_versions=(
                    ArtifactInputVersion(
                        entity_id=req_id,
                        version=req_v2.version,
                    ),
                    ArtifactInputVersion(
                        entity_id=source.artifact_id,
                        version=source.version,
                    ),
                ),
                hash="b" * 64,
                created_timestamp="2026-10-03T19:31:00Z",
            ),
        )

        restarted_projects = ProjectStore(projects.projects_root)
        lineage = ArtifactLineageStore(restarted_projects)
        lineage.verify_project(handle.project_id)
        dependencies = lineage.dependencies(
            handle.project_id,
            child.artifact_id,
            child.version,
        )

        if dependencies != child.input_dependency_versions:
            print("ARTIFACT_EDGES=FAIL reason=restart_mismatch")
            return 2

        registry = (
            handle.layout.project_dir
            / ARTIFACT_REGISTRY_DB_FILENAME
        )
        with sqlite3.connect(registry) as connection:
            edge_counts = dict(
                connection.execute(
                    """
                    SELECT dependency_kind, COUNT(*)
                    FROM artifact_dependency_edges
                    GROUP BY dependency_kind
                    """
                ).fetchall()
            )
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
            print("ARTIFACT_EDGES=FAIL reason=project_schema_changed")
            return 3

        print(
            "ARTIFACT_EDGES=PASS "
            f"child={child.artifact_id.value}@v{child.version} "
            f"dependencies={len(dependencies)} "
            f"requirement_edges={edge_counts.get('requirement', 0)} "
            f"artifact_edges={edge_counts.get('artifact', 0)} "
            "targets=verified "
            "restart=PASS "
            f"registry_tables={len(registry_tables)} "
            f"project_schema_tables={len(project_tables)} "
            "hashing=reserved_for_A4_4 "
            "states=reserved_for_A4_5"
        )
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
