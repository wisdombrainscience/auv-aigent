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
    MAX_PARALLEL_AGENT_TASKS,
    TASK_REGISTRY_DB_FILENAME,
    TaskConcurrencyScheduler,
    TaskCreateInput,
    TaskLifecycleStore,
    TaskPriorityQueue,
    TaskReadinessResolver,
    TaskStore,
)


def task_input(title: str, priority: int) -> TaskCreateInput:
    return TaskCreateInput(
        title=title,
        role="uav_systems_engineer",
        priority=priority,
        dependencies=(),
        input_artifacts=(),
        output_schema="engineering_task.v1",
        validators=("schema",),
        review_required=False,
        max_attempts=3,
    )


def queue(projects: ProjectStore, project_id) -> None:
    TaskReadinessResolver(projects).resolve_project(project_id)
    TaskPriorityQueue(projects).enqueue_ready(project_id)


def main() -> int:
    with tempfile.TemporaryDirectory(
        prefix="remote-uav-task-concurrency-"
    ) as raw:
        projects = ProjectStore(
            Path(raw) / "remote-uav" / "projects"
        )
        first_project = projects.create_project()
        second_project = projects.create_project()
        tasks = TaskStore(projects)

        low = tasks.create_task(
            first_project.project_id,
            task_input("Low", 100),
        )
        high = tasks.create_task(
            second_project.project_id,
            task_input("High", 900),
        )

        queue(projects, first_project.project_id)
        queue(projects, second_project.project_id)

        scheduler = TaskConcurrencyScheduler(projects)
        first = scheduler.assign_next()
        if (
            first is None
            or first.task.task_id != high.task_id
            or first.task.status is not TaskState.ASSIGNED
        ):
            print("TASK_CONCURRENCY=FAIL reason=global_priority")
            return 2

        if scheduler.assign_next() is not None:
            print("TASK_CONCURRENCY=FAIL reason=parallel_limit")
            return 3

        lifecycle = TaskLifecycleStore(projects)
        lifecycle.transition_task(
            second_project.project_id,
            high.task_id,
            TaskState.RUNNING,
            now_epoch_s=1000.0,
        )
        lifecycle.transition_task(
            second_project.project_id,
            high.task_id,
            TaskState.RESULT_PROPOSED,
        )

        second = scheduler.assign_next()
        if (
            second is None
            or second.task.task_id != low.task_id
            or second.task.status is not TaskState.ASSIGNED
        ):
            print("TASK_CONCURRENCY=FAIL reason=slot_release")
            return 4

        restarted = TaskConcurrencyScheduler(
            ProjectStore(projects.projects_root)
        )
        snapshot = restarted.snapshot()
        if (
            MAX_PARALLEL_AGENT_TASKS != 1
            or snapshot.active_count != 1
            or snapshot.active_tasks[0].task_id != low.task_id
        ):
            print("TASK_CONCURRENCY=FAIL reason=restart")
            return 5

        task_registry = (
            first_project.layout.data_directories["tasks"]
            / TASK_REGISTRY_DB_FILENAME
        )
        with sqlite3.connect(task_registry) as connection:
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
            print("TASK_CONCURRENCY=FAIL reason=project_schema_changed")
            return 6

        print(
            "TASK_CONCURRENCY=PASS "
            "limit=1 "
            f"first={high.task_id.value}:900 "
            "blocked_while_active=PASS "
            f"second={low.task_id.value}:100 "
            "global=PASS "
            "restart=PASS "
            "active=1 "
            f"registry_tables={len(registry_tables)} "
            f"project_schema_tables={len(project_tables)} "
            "cp5=READY"
        )
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
