#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
import sqlite3
import sys
import tempfile


_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from ai.contracts.task_states import TaskState  # noqa: E402
from ai.project.schema import PROJECT_SCHEMA_TABLES  # noqa: E402
from ai.project.store import ProjectStore  # noqa: E402
from ai.tasks import (  # noqa: E402
    TASK_REGISTRY_DB_FILENAME,
    TaskCreateInput,
    TaskLifecycleStore,
    TaskReadinessResolver,
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
        prefix="remote-uav-task-readiness-"
    ) as raw:
        projects = ProjectStore(
            Path(raw) / "remote-uav" / "projects"
        )
        handle = projects.create_project()
        tasks = TaskStore(projects)
        lifecycle = TaskLifecycleStore(projects)
        resolver = TaskReadinessResolver(projects)

        root = tasks.create_task(
            handle.project_id,
            task_input("Root"),
        )
        child = tasks.create_task(
            handle.project_id,
            task_input("Child", (root.task_id,)),
        )

        first = resolver.resolve_project(handle.project_id)
        if tasks.open_task(
            handle.project_id,
            root.task_id,
        ).status is not TaskState.READY:
            print("TASK_READINESS=FAIL reason=root_not_ready")
            return 2
        if tasks.open_task(
            handle.project_id,
            child.task_id,
        ).status is not TaskState.BLOCKED:
            print("TASK_READINESS=FAIL reason=child_not_blocked")
            return 3

        for target in (
            TaskState.QUEUED,
            TaskState.ASSIGNED,
            TaskState.RUNNING,
            TaskState.RESULT_PROPOSED,
            TaskState.VALIDATING,
            TaskState.DONE,
        ):
            lifecycle.transition_task(
                handle.project_id,
                root.task_id,
                target,
            )

        second = resolver.resolve_project(handle.project_id)
        if tasks.open_task(
            handle.project_id,
            child.task_id,
        ).status is not TaskState.READY:
            print("TASK_READINESS=FAIL reason=child_not_ready")
            return 4

        restarted_projects = ProjectStore(projects.projects_root)
        restarted_tasks = TaskStore(restarted_projects)
        if restarted_tasks.open_task(
            handle.project_id,
            child.task_id,
        ).status is not TaskState.READY:
            print("TASK_READINESS=FAIL reason=restart")
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
            print("TASK_READINESS=FAIL reason=project_schema_changed")
            return 6

        print(
            "TASK_READINESS=PASS "
            f"root={root.task_id.value}:DONE "
            f"child={child.task_id.value}:READY "
            f"first_ready={first.ready_count} "
            f"first_blocked={first.blocked_count} "
            f"second_changed={second.changed_count} "
            "restart=PASS "
            f"registry_tables={len(registry_tables)} "
            f"project_schema_tables={len(project_tables)} "
            "priority=reserved_for_T5_6 "
            "attempt_counter=reserved_for_T5_7"
        )
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
