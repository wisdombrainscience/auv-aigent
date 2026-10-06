#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path
import sqlite3
import sys
import tempfile

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from ai.agents import (  # noqa: E402
    AGENT_RESULT_OUTPUT_SCHEMA_ID,
    AGENT_RUN_REGISTRY_DB_FILENAME,
    AgentRunStore,
    SchemaEnforcedOutputValidator,
)
from ai.project.store import ProjectStore  # noqa: E402
from ai.providers.contracts import GenerateResponse  # noqa: E402
from ai.tasks import TaskCreateInput, TaskStore  # noqa: E402


def task_input(title: str) -> TaskCreateInput:
    return TaskCreateInput(
        title=title,
        role="uav_systems_engineer",
        priority=100,
        dependencies=(),
        input_artifacts=(),
        output_schema=AGENT_RESULT_OUTPUT_SCHEMA_ID,
        validators=("schema",),
        review_required=False,
        max_attempts=3,
    )


def response(statement: str) -> GenerateResponse:
    payload = {
        "status": "PROPOSED",
        "claims": [{"classification": "AI_PROPOSAL", "statement": statement}],
        "assumptions": [],
        "calculations": [],
        "artifacts": [],
        "risks": [],
        "missing_information": [],
        "open_questions": [],
    }
    return GenerateResponse(
        model="local-acceptance-model",
        content=json.dumps(payload, separators=(",", ":")),
        done=True,
    )


def user_tables(path: Path) -> tuple[str, ...]:
    with sqlite3.connect(path) as connection:
        return tuple(
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )
        )


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="remote-uav-agent-run-") as raw:
        projects = ProjectStore(Path(raw) / "remote-uav" / "projects")
        first_project = projects.create_project()
        second_project = projects.create_project()
        tasks = TaskStore(projects)
        validator = SchemaEnforcedOutputValidator(projects)
        runs = AgentRunStore(projects)

        first_task = tasks.create_task(first_project.project_id, task_input("First run"))
        second_task = tasks.create_task(second_project.project_id, task_input("Second run"))

        first_before = tasks.open_task(first_project.project_id, first_task.task_id)
        first_output = validator.enforce(
            first_project.project_id,
            first_task.task_id,
            response("First bounded proposal"),
        )
        second_output = validator.enforce(
            second_project.project_id,
            second_task.task_id,
            response("Second bounded proposal"),
        )

        first = runs.create_run(first_output)
        second = runs.create_run(second_output)
        first_after = tasks.open_task(first_project.project_id, first_task.task_id)

        if first.run_id.value != "AI-RUN-000001" or second.run_id.value != "AI-RUN-000002":
            print("AGENT_RUN_PERSISTENCE=FAIL reason=canonical_ids")
            return 2
        if first_before != first_after:
            print("AGENT_RUN_PERSISTENCE=FAIL reason=task_mutation")
            return 3
        if first.result.to_dict().get("status") != "PROPOSED":
            print("AGENT_RUN_PERSISTENCE=FAIL reason=proposal_status")
            return 4

        restarted = AgentRunStore(ProjectStore(projects.projects_root))
        reopened = restarted.open_run(first_project.project_id, first.run_id)
        if reopened != first:
            print("AGENT_RUN_PERSISTENCE=FAIL reason=restart")
            return 5

        registry = (
            first_project.layout.data_directories["ai_runs"]
            / AGENT_RUN_REGISTRY_DB_FILENAME
        )
        registry_tables = user_tables(registry)
        project_schema_tables = user_tables(first_project.database_path)
        if registry_tables != ("agent_run_registry_meta", "agent_runs"):
            print("AGENT_RUN_PERSISTENCE=FAIL reason=registry_tables")
            return 6
        if project_schema_tables != (
            "canonical_id_sequences",
            "project_schema_meta",
            "projects",
        ):
            print("AGENT_RUN_PERSISTENCE=FAIL reason=project_schema_tables")
            return 7

        print(
            "AGENT_RUN_PERSISTENCE=PASS "
            f"first={first.run_id.value} second={second.run_id.value} "
            f"task={first.task_id.value} schema={first.schema_id} "
            "proposal=PASS restart=PASS global_ids=PASS "
            "registry_tables=2 project_schema_tables=3 "
            "task_state=unchanged provider_calls=none "
            "manifest=reserved_for_G6_7 artifact=reserved_for_G6_8"
        )
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
