#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
import sqlite3
import sys
import tempfile

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from ai.artifacts import ArtifactInputVersion  # noqa: E402
from ai.contracts.task_states import TaskState  # noqa: E402
from ai.project.schema import PROJECT_SCHEMA_TABLES  # noqa: E402
from ai.project.store import ProjectStore  # noqa: E402
from ai.tasks import (  # noqa: E402
    TASK_REGISTRY_DB_FILENAME,
    TaskCreateInput,
    TaskStore,
)


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="remote-uav-task-persistence-") as raw:
        projects = ProjectStore(Path(raw) / "remote-uav" / "projects")
        handle = projects.create_project()
        store = TaskStore(projects)

        first = store.create_task(
            handle.project_id,
            TaskCreateInput(
                title="Requirements synthesis",
                role="requirements_engineer",
                priority=100,
                dependencies=(),
                input_artifacts=(),
                output_schema="requirements_summary.v1",
                validators=("schema",),
                review_required=True,
                max_attempts=3,
            ),
        )
        second = store.create_task(
            handle.project_id,
            TaskCreateInput(
                title="Mass budget",
                role="uav_systems_engineer",
                priority=90,
                dependencies=(first.task_id,),
                input_artifacts=(
                    ArtifactInputVersion(entity_id="ART-999999", version=1),
                ),
                output_schema="mass_budget.v1",
                validators=("schema", "finite_values"),
                review_required=True,
                max_attempts=3,
            ),
        )

        restarted = TaskStore(ProjectStore(projects.projects_root))
        reopened = restarted.open_task(handle.project_id, second.task_id)
        if reopened != second:
            print("TASK_PERSISTENCE=FAIL reason=restart")
            return 2
        if reopened.status is not TaskState.CREATED or reopened.attempt != 0:
            print("TASK_PERSISTENCE=FAIL reason=initial_state")
            return 3

        registry = handle.layout.data_directories["tasks"] / TASK_REGISTRY_DB_FILENAME
        with sqlite3.connect(registry) as connection:
            task_tables = tuple(
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
                )
            )
        with sqlite3.connect(projects.database_path) as connection:
            project_tables = tuple(
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
                )
            )

        if project_tables != PROJECT_SCHEMA_TABLES:
            print("TASK_PERSISTENCE=FAIL reason=project_schema_changed")
            return 4

        print(
            "TASK_PERSISTENCE=PASS "
            f"tasks={len(restarted.list_tasks(handle.project_id))} "
            f"first={first.task_id.value} "
            f"second={second.task_id.value} "
            "status=CREATED "
            "attempt=0 "
            "restart=PASS "
            f"registry_tables={len(task_tables)} "
            f"project_schema_tables={len(project_tables)} "
            "state_machine=reserved_for_T5_2 "
            "dependencies=reserved_for_T5_3"
        )
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
