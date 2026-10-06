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
    TaskPriorityQueue,
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


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="remote-uav-priority-queue-") as raw:
        projects = ProjectStore(Path(raw) / "remote-uav" / "projects")
        handle = projects.create_project()
        tasks = TaskStore(projects)
        queue = TaskPriorityQueue(projects)

        low = tasks.create_task(handle.project_id, task_input("Low", 10))
        high = tasks.create_task(handle.project_id, task_input("High", 900))
        tie_a = tasks.create_task(handle.project_id, task_input("Tie A", 100))
        tie_b = tasks.create_task(handle.project_id, task_input("Tie B", 100))

        report = queue.enqueue_ready(handle.project_id)
        order = tuple(item.task_id for item in report.items)
        expected = (high.task_id, tie_a.task_id, tie_b.task_id, low.task_id)
        if order != expected:
            print("TASK_PRIORITY_QUEUE=FAIL reason=order")
            return 2
        if any(
            tasks.open_task(handle.project_id, item.task_id).status is not TaskState.QUEUED
            for item in report.items
        ):
            print("TASK_PRIORITY_QUEUE=FAIL reason=status")
            return 3
        if any(
            tasks.open_task(handle.project_id, item.task_id).attempt != 0
            for item in report.items
        ):
            print("TASK_PRIORITY_QUEUE=FAIL reason=attempt")
            return 4

        restarted = TaskPriorityQueue(ProjectStore(projects.projects_root))
        if tuple(item.task_id for item in restarted.ordered_queue(handle.project_id)) != expected:
            print("TASK_PRIORITY_QUEUE=FAIL reason=restart")
            return 5

        registry = handle.layout.data_directories["tasks"] / TASK_REGISTRY_DB_FILENAME
        with sqlite3.connect(registry) as connection:
            registry_tables = tuple(
                row[0] for row in connection.execute(
                    """
                    SELECT name FROM sqlite_master
                    WHERE type='table' AND name NOT LIKE 'sqlite_%'
                    ORDER BY name
                    """
                )
            )
        with sqlite3.connect(projects.database_path) as connection:
            project_tables = tuple(
                row[0] for row in connection.execute(
                    """
                    SELECT name FROM sqlite_master
                    WHERE type='table' AND name NOT LIKE 'sqlite_%'
                    ORDER BY name
                    """
                )
            )
        if project_tables != PROJECT_SCHEMA_TABLES:
            print("TASK_PRIORITY_QUEUE=FAIL reason=project_schema_changed")
            return 6

        print(
            "TASK_PRIORITY_QUEUE=PASS "
            "queued=4 "
            f"first={high.task_id.value}:900 "
            "ties=canonical_id "
            "attempt=0 "
            "restart=PASS "
            f"registry_tables={len(registry_tables)} "
            f"project_schema_tables={len(project_tables)} "
            "attempt_counter=reserved_for_T5_7 "
            "concurrency=reserved_for_T5_11"
        )
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
