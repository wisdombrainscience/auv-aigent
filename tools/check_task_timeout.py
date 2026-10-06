#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
import sqlite3
import sys
import tempfile

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from ai.contracts.task_states import TaskState
from ai.project.schema import PROJECT_SCHEMA_TABLES
from ai.project.store import ProjectStore
from ai.tasks import (
    TASK_REGISTRY_DB_FILENAME,
    TaskCreateInput,
    TaskLifecycleStore,
    TaskPriorityQueue,
    TaskReadinessResolver,
    TaskStore,
    TaskTimeoutStore,
)


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="remote-uav-task-timeout-") as raw:
        projects = ProjectStore(Path(raw) / "remote-uav" / "projects")
        handle = projects.create_project()
        tasks = TaskStore(projects)
        lifecycle = TaskLifecycleStore(projects)
        task = tasks.create_task(
            handle.project_id,
            TaskCreateInput(
                title="Timeout acceptance",
                role="uav_systems_engineer",
                priority=100,
                dependencies=(),
                input_artifacts=(),
                output_schema="engineering_task.v1",
                validators=("schema",),
                review_required=False,
                max_attempts=3,
            ),
        )
        TaskReadinessResolver(projects).resolve_project(handle.project_id)
        TaskPriorityQueue(projects).enqueue_ready(handle.project_id)
        lifecycle.transition_task(handle.project_id, task.task_id, TaskState.ASSIGNED)
        lifecycle.transition_task(
            handle.project_id,
            task.task_id,
            TaskState.RUNNING,
            now_epoch_s=1000.0,
        )

        restarted_projects = ProjectStore(projects.projects_root)
        timeouts = TaskTimeoutStore(restarted_projects)
        window = timeouts.window(handle.project_id, task.task_id)
        if window.deadline_at_epoch_s != 1300.0:
            print("TASK_TIMEOUT=FAIL reason=deadline")
            return 2
        if timeouts.expire_overdue(handle.project_id, now_epoch_s=1299.0).expired_count != 0:
            print("TASK_TIMEOUT=FAIL reason=early")
            return 3
        report = timeouts.expire_overdue(handle.project_id, now_epoch_s=1300.0)
        reopened = TaskStore(restarted_projects).open_task(handle.project_id, task.task_id)
        if report.expired_count != 1 or reopened.status is not TaskState.NEEDS_HUMAN:
            print("TASK_TIMEOUT=FAIL reason=expiry")
            return 4
        if report.decisions[0].failure.code != "TASK_TIMEOUT":
            print("TASK_TIMEOUT=FAIL reason=failure_code")
            return 5

        registry = handle.layout.data_directories["tasks"] / TASK_REGISTRY_DB_FILENAME
        with sqlite3.connect(registry) as connection:
            registry_tables = tuple(
                row[0] for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' "
                    "AND name NOT LIKE 'sqlite_%' ORDER BY name"
                )
            )
        with sqlite3.connect(projects.database_path) as connection:
            project_tables = tuple(
                row[0] for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' "
                    "AND name NOT LIKE 'sqlite_%' ORDER BY name"
                )
            )
        if project_tables != PROJECT_SCHEMA_TABLES:
            print("TASK_TIMEOUT=FAIL reason=project_schema_changed")
            return 6

        print(
            "TASK_TIMEOUT=PASS "
            f"task={task.task_id.value} "
            "timeout_s=300 "
            "before_deadline=RUNNING "
            "at_deadline=NEEDS_HUMAN "
            "failure=TASK_TIMEOUT "
            "restart=PASS "
            f"registry_tables={len(registry_tables)} "
            f"project_schema_tables={len(project_tables)} "
            "interrupted=reserved_for_T5_9"
        )
        return 0

if __name__ == "__main__":
    raise SystemExit(main())
