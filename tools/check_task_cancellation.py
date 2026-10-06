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
    TaskCancellationStore,
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
        prefix="remote-uav-task-cancellation-"
    ) as raw:
        projects = ProjectStore(Path(raw) / "remote-uav" / "projects")
        handle = projects.create_project()
        tasks = TaskStore(projects)
        lifecycle = TaskLifecycleStore(projects)
        readiness = TaskReadinessResolver(projects)
        cancellation = TaskCancellationStore(projects)

        root = tasks.create_task(handle.project_id, task_input("Root"))
        running = tasks.create_task(handle.project_id, task_input("Running"))
        child = tasks.create_task(
            handle.project_id,
            task_input("Child", (root.task_id,)),
        )
        leaf = tasks.create_task(
            handle.project_id,
            task_input("Leaf", (child.task_id,)),
        )
        unrelated = tasks.create_task(
            handle.project_id,
            task_input("Unrelated"),
        )

        for target in (
            TaskState.READY,
            TaskState.QUEUED,
            TaskState.ASSIGNED,
        ):
            lifecycle.transition_task(
                handle.project_id,
                running.task_id,
                target,
            )
        lifecycle.transition_task(
            handle.project_id,
            running.task_id,
            TaskState.RUNNING,
            now_epoch_s=1000.0,
        )

        readiness.resolve_project(handle.project_id)
        cascade = cancellation.cancel_task(
            handle.project_id,
            root.task_id,
            cascade_dependents=True,
        )
        exact = cancellation.cancel_task(
            handle.project_id,
            running.task_id,
        )

        if cascade.cancelled_count != 3:
            print("TASK_CANCELLATION=FAIL reason=cascade_count")
            return 2
        if exact.cancelled_count != 1:
            print("TASK_CANCELLATION=FAIL reason=running_cancel")
            return 3

        restarted_projects = ProjectStore(projects.projects_root)
        restarted_tasks = TaskStore(restarted_projects)
        expected_cancelled = (root, running, child, leaf)
        for task in expected_cancelled:
            if restarted_tasks.open_task(
                handle.project_id,
                task.task_id,
            ).status is not TaskState.CANCELLED:
                print("TASK_CANCELLATION=FAIL reason=restart_status")
                return 4
        if restarted_tasks.open_task(
            handle.project_id,
            unrelated.task_id,
        ).status is not TaskState.READY:
            print("TASK_CANCELLATION=FAIL reason=unrelated_changed")
            return 5
        running_after = restarted_tasks.open_task(
            handle.project_id,
            running.task_id,
        )
        if running_after.attempt != 1:
            print("TASK_CANCELLATION=FAIL reason=attempt_changed")
            return 6

        registry = (
            handle.layout.data_directories["tasks"]
            / TASK_REGISTRY_DB_FILENAME
        )
        with sqlite3.connect(registry) as connection:
            timing = connection.execute(
                """
                SELECT execution_attempt,
                       started_at_epoch_s,
                       deadline_at_epoch_s
                FROM tasks
                WHERE task_id=?
                """,
                (running.task_id.value,),
            ).fetchone()
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
        if timing != (None, None, None):
            print("TASK_CANCELLATION=FAIL reason=timing_not_cleared")
            return 7

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
            print("TASK_CANCELLATION=FAIL reason=project_schema_changed")
            return 8

        print(
            "TASK_CANCELLATION=PASS "
            f"root={root.task_id.value}:CANCELLED "
            "descendants=2 "
            f"running={running.task_id.value}:CANCELLED "
            "metadata=cleared "
            "attempt=1 "
            "unrelated=READY "
            "restart=PASS "
            f"registry_tables={len(registry_tables)} "
            f"project_schema_tables={len(project_tables)} "
            "concurrency=reserved_for_T5_11"
        )
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
