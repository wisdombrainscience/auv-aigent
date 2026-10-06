#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
import sqlite3
import sys
import tempfile

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from ai.project.schema import PROJECT_SCHEMA_TABLES  # noqa: E402
from ai.project.store import ProjectStore  # noqa: E402
from ai.tasks import (  # noqa: E402
    TASK_REGISTRY_DB_FILENAME,
    TaskCreateInput,
    TaskDependencyStore,
    TaskStore,
)


def task_input(title: str, dependencies=()) -> TaskCreateInput:
    return TaskCreateInput(
        title=title,
        role="uav_systems_engineer",
        priority=100,
        dependencies=tuple(dependencies),
        input_artifacts=(),
        output_schema="engineering_task.v1",
        validators=("schema",),
        review_required=False,
        max_attempts=3,
    )


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="remote-uav-task-deps-") as raw:
        projects = ProjectStore(Path(raw) / "remote-uav" / "projects")
        handle = projects.create_project()
        tasks = TaskStore(projects)
        root = tasks.create_task(handle.project_id, task_input("Root"))
        child = tasks.create_task(
            handle.project_id,
            task_input("Child", dependencies=(root.task_id,)),
        )

        dependencies = TaskDependencyStore(projects)
        dependencies.verify_project(handle.project_id)
        direct = dependencies.dependencies(handle.project_id, child.task_id)
        reverse = dependencies.direct_dependents(handle.project_id, root.task_id)
        if direct != (root.task_id,) or reverse != (child.task_id,):
            print("TASK_DEPENDENCIES=FAIL reason=edge_query")
            return 2

        restarted_projects = ProjectStore(projects.projects_root)
        restarted = TaskDependencyStore(restarted_projects)
        restarted.verify_project(handle.project_id)
        if restarted.dependencies(handle.project_id, child.task_id) != (root.task_id,):
            print("TASK_DEPENDENCIES=FAIL reason=restart")
            return 3

        registry = handle.layout.data_directories["tasks"] / TASK_REGISTRY_DB_FILENAME
        with sqlite3.connect(registry) as connection:
            registry_tables = tuple(
                row[0] for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' "
                    "AND name NOT LIKE 'sqlite_%' ORDER BY name"
                )
            )
            edge_count = connection.execute(
                "SELECT COUNT(*) FROM task_dependency_edges"
            ).fetchone()[0]
        with sqlite3.connect(projects.database_path) as connection:
            project_tables = tuple(
                row[0] for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' "
                    "AND name NOT LIKE 'sqlite_%' ORDER BY name"
                )
            )

        if project_tables != PROJECT_SCHEMA_TABLES:
            print("TASK_DEPENDENCIES=FAIL reason=project_schema_changed")
            return 4

        print(
            "TASK_DEPENDENCIES=PASS "
            f"root={root.task_id.value} "
            f"child={child.task_id.value} "
            f"edges={edge_count} "
            "existence=PASS "
            "reverse=PASS "
            "restart=PASS "
            f"registry_tables={len(registry_tables)} "
            f"project_schema_tables={len(project_tables)} "
            "cycles=reserved_for_T5_4 "
            "ready=reserved_for_T5_5"
        )
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
