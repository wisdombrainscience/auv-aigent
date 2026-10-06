#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from ai.agents import (
    AGENT_RESULT_OUTPUT_SCHEMA_ID,
    AgentRunStore,
    ContextManifestStore,
    MinimalContextBuilder,
    ResultDraftArtifactStore,
    SchemaEnforcedOutputValidator,
)
from ai.artifacts import ArtifactVersionStore
from ai.contracts.artifact_states import ArtifactState
from ai.contracts.validation_result import ValidationStatus
from ai.project.store import ProjectStore
from ai.providers.contracts import GenerateResponse
from ai.tasks import TaskCreateInput, TaskStore
from ai.validators import DeterministicMassBudgetValidator


CREATED_AT = "2026-10-05T13:30:00Z"


def task_input():
    return TaskCreateInput(
        title="Acceptance mass budget",
        role="uav_systems_engineer",
        priority=100,
        dependencies=(),
        input_artifacts=(),
        output_schema=AGENT_RESULT_OUTPUT_SCHEMA_ID,
        validators=("schema", "finite_values", "units", "mass_budget"),
        review_required=False,
        max_attempts=3,
    )


def response(calculation):
    payload = {
        "status": "PROPOSED",
        "claims": [
            {
                "classification": "CALCULATED_VALUE",
                "statement": "Acceptance mass budget.",
            }
        ],
        "assumptions": [],
        "calculations": [calculation],
        "artifacts": [],
        "risks": [],
        "missing_information": [],
        "open_questions": [],
    }
    return GenerateResponse(
        model="local-test",
        content=json.dumps(payload, separators=(",", ":")),
        done=True,
    )


def budget(*, reported=3.5, limit=4.0):
    return {
        "kind": "mass_budget",
        "components": [
            {"name": "airframe", "mass_kg": 1.8},
            {"name": "battery", "mass_kg": 1.1},
            {"name": "payload", "mass_kg": 0.6},
        ],
        "reported_total_kg": reported,
        "limit_kg": limit,
    }


def create_artifact(projects, handle, calculation):
    tasks = TaskStore(projects)
    task = tasks.create_task(handle.project_id, task_input())
    context = MinimalContextBuilder(projects).build(handle.project_id, task.task_id)
    output = SchemaEnforcedOutputValidator(projects).enforce(
        handle.project_id, task.task_id, response(calculation)
    )
    run = AgentRunStore(projects).create_run(output)
    ContextManifestStore(projects).create_manifest(run, context)
    artifact = ResultDraftArtifactStore(projects).create_artifact(
        handle.project_id, run.run_id, created_timestamp=CREATED_AT
    )
    return task, artifact


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="ruav-v7-4-") as temp:
        projects = ProjectStore(Path(temp) / "projects")
        handle = projects.create_project()

        task, artifact = create_artifact(projects, handle, budget())
        validator = DeterministicMassBudgetValidator(projects)
        valid = validator.validate(
            handle.project_id, artifact.record.artifact_id, artifact.record.version
        )
        if valid.overall is not ValidationStatus.PASS:
            raise SystemExit("valid mass budget did not PASS")
        details = valid.checks[0].to_dict()["details"]
        if details["computed_total_kg"] != 3.5 or details["limit_kg"] != 4:
            raise SystemExit("mass budget evidence mismatch")

        _task2, over_artifact = create_artifact(projects, handle, budget(limit=3.4))
        over = validator.validate(
            handle.project_id, over_artifact.record.artifact_id, 1
        )
        if over.overall is not ValidationStatus.FAIL:
            raise SystemExit("over-limit budget was not rejected")

        _task3, mismatch_artifact = create_artifact(
            projects, handle, budget(reported=3.6)
        )
        mismatch = validator.validate(
            handle.project_id, mismatch_artifact.record.artifact_id, 1
        )
        if mismatch.overall is not ValidationStatus.FAIL:
            raise SystemExit("mismatched reported total was not rejected")

        reopened = ArtifactVersionStore(projects).open_version(
            handle.project_id, artifact.record.artifact_id, 1
        )
        if reopened.status is not ArtifactState.DRAFT or reopened.validation_result is not None:
            raise SystemExit("V7.4 changed artifact lifecycle/persisted validation")

        tasks = TaskStore(projects)
        before = tasks.open_task(handle.project_id, task.task_id)

        restarted = ProjectStore(projects.projects_root)
        again = DeterministicMassBudgetValidator(restarted).validate(
            handle.project_id, artifact.record.artifact_id, 1
        )
        if again != valid:
            raise SystemExit("restart changed mass-budget result")
        if TaskStore(restarted).open_task(handle.project_id, task.task_id) != before:
            raise SystemExit("V7.4 changed task state")

        print(
            "MASS_BUDGET=PASS "
            f"artifact={artifact.record.artifact_id.value}@v1 "
            "check=mass_budget status=PASS components=3 computed_kg=3.5 limit_kg=4 "
            "over_limit=FAIL mismatch=FAIL restart=PASS artifact_state=DRAFT "
            "persisted_validation=none task_state=unchanged provider_calls=none "
            "battery_energy=reserved_for_V7_5 lifecycle_transition=reserved cp7=pending"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
