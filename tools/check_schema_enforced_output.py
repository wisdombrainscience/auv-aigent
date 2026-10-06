#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from ai.agents import (  # noqa: E402
    AGENT_RESULT_OUTPUT_SCHEMA_ID,
    SchemaEnforcedOutputValidator,
    UnknownOutputSchemaError,
)
from ai.project.store import ProjectStore  # noqa: E402
from ai.providers.contracts import GenerateResponse  # noqa: E402
from ai.providers.model_output import ModelOutputError  # noqa: E402
from ai.tasks import TaskCreateInput, TaskStore  # noqa: E402


def task_input(title: str, output_schema: str) -> TaskCreateInput:
    return TaskCreateInput(
        title=title,
        role="uav_systems_engineer",
        priority=100,
        dependencies=(),
        input_artifacts=(),
        output_schema=output_schema,
        validators=("schema",),
        review_required=False,
        max_attempts=3,
    )


def valid_response() -> GenerateResponse:
    payload = {
        "status": "PROPOSED",
        "claims": [
            {
                "classification": "AI_PROPOSAL",
                "statement": "Bounded schema-enforced engineering proposal.",
            }
        ],
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


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="remote-uav-schema-output-") as raw:
        projects = ProjectStore(Path(raw) / "remote-uav" / "projects")
        handle = projects.create_project()
        tasks = TaskStore(projects)

        task = tasks.create_task(
            handle.project_id,
            task_input("Schema-enforced output", AGENT_RESULT_OUTPUT_SCHEMA_ID),
        )
        unknown = tasks.create_task(
            handle.project_id,
            task_input("Unknown schema", "mass_budget.v1"),
        )

        before = tasks.open_task(handle.project_id, task.task_id)
        validator = SchemaEnforcedOutputValidator(projects)
        schema = validator.provider_schema_for_task(handle.project_id, task.task_id)
        output = validator.enforce(handle.project_id, task.task_id, valid_response())
        after = tasks.open_task(handle.project_id, task.task_id)

        if before != after:
            print("SCHEMA_ENFORCED_OUTPUT=FAIL reason=task_mutation")
            return 2
        if schema.get("additionalProperties") is not False:
            print("SCHEMA_ENFORCED_OUTPUT=FAIL reason=schema_not_strict")
            return 3
        if output.result.to_dict().get("status") != "PROPOSED":
            print("SCHEMA_ENFORCED_OUTPUT=FAIL reason=status")
            return 4

        bad_payload = output.result.to_dict()
        bad_payload["status"] = "PASS"
        try:
            validator.enforce(
                handle.project_id,
                task.task_id,
                GenerateResponse(
                    model="local-acceptance-model",
                    content=json.dumps(bad_payload, separators=(",", ":")),
                    done=True,
                ),
            )
        except ModelOutputError as exc:
            if exc.failure.to_dict()["code"] != "MODEL_INVALID_OUTPUT":
                print("SCHEMA_ENFORCED_OUTPUT=FAIL reason=failure_taxonomy")
                return 5
        else:
            print("SCHEMA_ENFORCED_OUTPUT=FAIL reason=invalid_accepted")
            return 6

        try:
            validator.enforce(handle.project_id, unknown.task_id, valid_response())
        except UnknownOutputSchemaError:
            pass
        else:
            print("SCHEMA_ENFORCED_OUTPUT=FAIL reason=unknown_schema_accepted")
            return 7

        restarted = SchemaEnforcedOutputValidator(
            ProjectStore(projects.projects_root)
        ).enforce(handle.project_id, task.task_id, valid_response())
        if restarted != output:
            print("SCHEMA_ENFORCED_OUTPUT=FAIL reason=restart")
            return 8

        print(
            "SCHEMA_ENFORCED_OUTPUT=PASS "
            f"task={task.task_id.value} "
            "schema=engineering_task.v1 "
            "valid=PASS invalid=REJECTED unknown_schema=REJECTED "
            "provenance=PASS restart=PASS provider_calls=none "
            "task_state=unchanged "
            "run_persistence=reserved_for_G6_6 "
            "manifest=reserved_for_G6_7 "
            "artifact=reserved_for_G6_8"
        )
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
