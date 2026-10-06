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
    TaskStore,
)


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="remote-uav-task-states-") as raw:
        projects = ProjectStore(Path(raw) / "remote-uav" / "projects")
        handle = projects.create_project()
        tasks = TaskStore(projects)
        record = tasks.create_task(
            handle.project_id,
            TaskCreateInput(
                title="Acceptance task",
                role="reviewer",
                priority=10,
                dependencies=(),
                input_artifacts=(),
                output_schema="review.v1",
                validators=(),
                review_required=False,
                max_attempts=3,
            ),
        )
        lifecycle = TaskLifecycleStore(projects)
        path = [
            TaskState.READY,
            TaskState.QUEUED,
            TaskState.ASSIGNED,
            TaskState.RUNNING,
            TaskState.RESULT_PROPOSED,
            TaskState.VALIDATING,
            TaskState.DONE,
        ]
        current = record
        for state in path:
            current = lifecycle.transition_task(handle.project_id, record.task_id, state)

        restarted = TaskStore(ProjectStore(projects.projects_root)).open_task(
            handle.project_id,
            record.task_id,
        )
        if restarted.status is not TaskState.DONE:
            print("TASK_STATES=FAIL reason=restart")
            return 2
        if restarted.attempt != 1:
            print("TASK_STATES=FAIL reason=attempt_changed")
            return 3

        registry = handle.layout.data_directories["tasks"] / TASK_REGISTRY_DB_FILENAME
        with sqlite3.connect(registry) as connection:
            registry_tables = tuple(
                row[0] for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
                )
            )
        with sqlite3.connect(projects.database_path) as connection:
            project_tables = tuple(
                row[0] for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
                )
            )
        if project_tables != PROJECT_SCHEMA_TABLES:
            print("TASK_STATES=FAIL reason=project_schema_changed")
            return 4

        print(
            "TASK_STATES=PASS "
            f"task={record.task_id.value} "
            "path=CREATED-READY-QUEUED-ASSIGNED-RUNNING-RESULT_PROPOSED-VALIDATING-DONE "
            "attempt=1 "
            "restart=PASS "
            f"registry_tables={len(registry_tables)} "
            f"project_schema_tables={len(project_tables)} "
            "dependencies=reserved_for_T5_3 "
            "attempt_counter=reserved_for_T5_7"
        )
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
