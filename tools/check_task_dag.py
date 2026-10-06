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
    TaskDAGCycleError,
    TaskDAGValidator,
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
    with tempfile.TemporaryDirectory(
        prefix="remote-uav-task-dag-"
    ) as raw:
        projects = ProjectStore(
            Path(raw) / "remote-uav" / "projects"
        )
        handle = projects.create_project()
        tasks = TaskStore(projects)

        root = tasks.create_task(
            handle.project_id,
            task_input("Root"),
        )
        left = tasks.create_task(
            handle.project_id,
            task_input("Left", (root.task_id,)),
        )
        right = tasks.create_task(
            handle.project_id,
            task_input("Right", (root.task_id,)),
        )
        join = tasks.create_task(
            handle.project_id,
            task_input("Join", (left.task_id, right.task_id)),
        )

        validator = TaskDAGValidator(projects)
        report = validator.validate_project(handle.project_id)
        expected = (
            root.task_id,
            left.task_id,
            right.task_id,
            join.task_id,
        )
        if report.topological_order != expected:
            print("TASK_DAG=FAIL reason=topological_order")
            return 2

        restarted = TaskDAGValidator(
            ProjectStore(projects.projects_root)
        )
        if restarted.validate_project(handle.project_id) != report:
            print("TASK_DAG=FAIL reason=restart")
            return 3

        cycle_project = projects.create_project()
        cycle_tasks = TaskStore(projects)
        self_task = cycle_tasks.create_task(
            cycle_project.project_id,
            task_input("Self", ("TASK-000005",)),
        )
        try:
            TaskDAGValidator(projects).validate_project(
                cycle_project.project_id
            )
        except TaskDAGCycleError as exc:
            if exc.cycle.task_ids != (
                self_task.task_id,
                self_task.task_id,
            ):
                print("TASK_DAG=FAIL reason=cycle_witness")
                return 4
        else:
            print("TASK_DAG=FAIL reason=self_cycle_not_detected")
            return 5

        registry = (
            handle.layout.data_directories["tasks"]
            / TASK_REGISTRY_DB_FILENAME
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
            print("TASK_DAG=FAIL reason=project_schema_changed")
            return 6

        print(
            "TASK_DAG=PASS "
            "nodes=4 "
            "edges=4 "
            "topological=PASS "
            "diamond=PASS "
            "self_cycle=PASS "
            "restart=PASS "
            f"registry_tables={len(registry_tables)} "
            f"project_schema_tables={len(project_tables)} "
            "ready=reserved_for_T5_5"
        )
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
