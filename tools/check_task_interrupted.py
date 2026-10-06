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
    TaskInterruptedRecoveryStore,
    TaskLifecycleStore,
    TaskPriorityQueue,
    TaskReadinessResolver,
    TaskStore,
)


def main() -> int:
    with tempfile.TemporaryDirectory(
        prefix="remote-uav-task-interrupted-"
    ) as raw:
        projects = ProjectStore(
            Path(raw) / "remote-uav" / "projects"
        )
        handle = projects.create_project()
        tasks = TaskStore(projects)
        lifecycle = TaskLifecycleStore(projects)
        readiness = TaskReadinessResolver(projects)
        queue = TaskPriorityQueue(projects)
        recovery = TaskInterruptedRecoveryStore(projects)

        task = tasks.create_task(
            handle.project_id,
            TaskCreateInput(
                title="Interrupted recovery acceptance",
                role="uav_systems_engineer",
                priority=100,
                dependencies=(),
                input_artifacts=(),
                output_schema="engineering_task.v1",
                validators=("schema",),
                review_required=False,
                max_attempts=2,
            ),
        )

        def start_attempt(now_value: float) -> None:
            readiness.resolve_project(handle.project_id)
            queue.enqueue_ready(handle.project_id)
            lifecycle.transition_task(
                handle.project_id,
                task.task_id,
                TaskState.ASSIGNED,
            )
            lifecycle.transition_task(
                handle.project_id,
                task.task_id,
                TaskState.RUNNING,
                now_epoch_s=now_value,
            )

        start_attempt(1000.0)

        restarted_projects = ProjectStore(projects.projects_root)
        first_recovery_store = TaskInterruptedRecoveryStore(
            restarted_projects
        )
        first = first_recovery_store.recover_project(
            handle.project_id,
            observed_at_epoch_s=1100.0,
        )
        first_task = TaskStore(restarted_projects).open_task(
            handle.project_id,
            task.task_id,
        )
        if (
            first.recovered_count != 1
            or first_task.status is not TaskState.INTERRUPTED
            or first_task.attempt != 1
        ):
            print("TASK_INTERRUPTED=FAIL reason=first_recovery")
            return 2

        first_route = first_recovery_store.route_interrupted(
            handle.project_id,
            task.task_id,
        )
        if first_route.target_status is not TaskState.BLOCKED:
            print("TASK_INTERRUPTED=FAIL reason=retry_route")
            return 3

        # Continue using restarted stores.
        restarted_readiness = TaskReadinessResolver(restarted_projects)
        restarted_queue = TaskPriorityQueue(restarted_projects)
        restarted_lifecycle = TaskLifecycleStore(restarted_projects)

        restarted_readiness.resolve_project(handle.project_id)
        restarted_queue.enqueue_ready(handle.project_id)
        restarted_lifecycle.transition_task(
            handle.project_id,
            task.task_id,
            TaskState.ASSIGNED,
        )
        restarted_lifecycle.transition_task(
            handle.project_id,
            task.task_id,
            TaskState.RUNNING,
            now_epoch_s=1200.0,
        )

        second_restart = ProjectStore(projects.projects_root)
        second_recovery_store = TaskInterruptedRecoveryStore(
            second_restart
        )
        second = second_recovery_store.recover_project(
            handle.project_id,
            observed_at_epoch_s=1250.0,
        )
        second_route = second_recovery_store.route_interrupted(
            handle.project_id,
            task.task_id,
        )

        final = TaskStore(second_restart).open_task(
            handle.project_id,
            task.task_id,
        )
        if (
            second.recovered_count != 1
            or second_route.target_status is not TaskState.NEEDS_HUMAN
            or final.status is not TaskState.NEEDS_HUMAN
            or final.attempt != 2
        ):
            print("TASK_INTERRUPTED=FAIL reason=exhausted_route")
            return 4

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
                (task.task_id.value,),
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
            print("TASK_INTERRUPTED=FAIL reason=metadata_not_cleared")
            return 5

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
            print("TASK_INTERRUPTED=FAIL reason=project_schema_changed")
            return 6

        print(
            "TASK_INTERRUPTED=PASS "
            f"task={task.task_id.value} "
            "recoveries=2 "
            "first=INTERRUPTED "
            "retry=BLOCKED "
            "exhausted=NEEDS_HUMAN "
            "attempts=2 "
            "restart=PASS "
            "metadata=cleared "
            f"registry_tables={len(registry_tables)} "
            f"project_schema_tables={len(project_tables)} "
            "cancellation=reserved_for_T5_10 "
            "concurrency=reserved_for_T5_11"
        )
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
