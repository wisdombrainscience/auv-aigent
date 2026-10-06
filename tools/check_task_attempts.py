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
    TaskAttemptStore,
    TaskCreateInput,
    TaskLifecycleStore,
    TaskPriorityQueue,
    TaskReadinessResolver,
    TaskStore,
)


def main() -> int:
    with tempfile.TemporaryDirectory(
        prefix="remote-uav-task-attempts-"
    ) as raw:
        projects = ProjectStore(
            Path(raw) / "remote-uav" / "projects"
        )
        handle = projects.create_project()
        tasks = TaskStore(projects)
        lifecycle = TaskLifecycleStore(projects)
        attempts = TaskAttemptStore(projects)
        readiness = TaskReadinessResolver(projects)
        queue = TaskPriorityQueue(projects)

        task = tasks.create_task(
            handle.project_id,
            TaskCreateInput(
                title="Bounded attempt acceptance",
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

        observed = []
        for number in (1, 2, 3):
            readiness.resolve_project(handle.project_id)
            queue.enqueue_ready(handle.project_id)
            lifecycle.transition_task(
                handle.project_id,
                task.task_id,
                TaskState.ASSIGNED,
            )
            running = lifecycle.transition_task(
                handle.project_id,
                task.task_id,
                TaskState.RUNNING,
            )
            observed.append(running.attempt)
            lifecycle.transition_task(
                handle.project_id,
                task.task_id,
                TaskState.RESULT_PROPOSED,
            )
            lifecycle.transition_task(
                handle.project_id,
                task.task_id,
                TaskState.REWORK,
            )
            route = attempts.route_rework(
                handle.project_id,
                task.task_id,
            )
            if number < 3 and route.target_status is not TaskState.BLOCKED:
                print("TASK_ATTEMPTS=FAIL reason=early_human_gate")
                return 2
            if number == 3 and route.target_status is not TaskState.NEEDS_HUMAN:
                print("TASK_ATTEMPTS=FAIL reason=missing_human_gate")
                return 3

        if observed != [1, 2, 3]:
            print("TASK_ATTEMPTS=FAIL reason=increment_sequence")
            return 4

        restarted_projects = ProjectStore(projects.projects_root)
        reopened = TaskStore(restarted_projects).open_task(
            handle.project_id,
            task.task_id,
        )
        budget = TaskAttemptStore(restarted_projects).budget(
            handle.project_id,
            task.task_id,
        )
        if (
            reopened.status is not TaskState.NEEDS_HUMAN
            or reopened.attempt != 3
            or not budget.exhausted
        ):
            print("TASK_ATTEMPTS=FAIL reason=restart_budget")
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
            print("TASK_ATTEMPTS=FAIL reason=project_schema_changed")
            return 6

        print(
            "TASK_ATTEMPTS=PASS "
            f"task={task.task_id.value} "
            "attempts=1-2-3 "
            "max=3 "
            "exhausted=NEEDS_HUMAN "
            "restart=PASS "
            f"registry_tables={len(registry_tables)} "
            f"project_schema_tables={len(project_tables)} "
            "timeout=reserved_for_T5_8 "
            "interrupted=reserved_for_T5_9"
        )
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
