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
from ai.validators import DeterministicThrustToWeightValidator


CREATED_AT = "2026-10-05T18:30:00Z"


def task_input():
    return TaskCreateInput(
        title="Acceptance thrust-to-weight",
        role="uav_systems_engineer",
        priority=100,
        dependencies=(),
        input_artifacts=(),
        output_schema=AGENT_RESULT_OUTPUT_SCHEMA_ID,
        validators=("schema", "finite_values", "units", "thrust_to_weight"),
        review_required=False,
        max_attempts=3,
    )


def calculation(*, reported=2):
    return {
        "kind": "thrust_to_weight",
        "total_thrust_n": 68.64655,
        "mass_kg": 3.5,
        "gravity_m_s2": 9.80665,
        "reported_thrust_to_weight": reported,
    }


def response(value):
    payload = {
        "status": "PROPOSED",
        "claims": [
            {
                "classification": "CALCULATED_VALUE",
                "statement": "Acceptance thrust-to-weight.",
            }
        ],
        "assumptions": [],
        "calculations": [value],
        "artifacts": [],
        "risks": [],
        "missing_information": [],
        "open_questions": [],
    }
    return GenerateResponse(
        model="local-test",
        content=json.dumps(payload, separators=(",", ":"), allow_nan=False),
        done=True,
    )


def create_artifact(projects, handle, value):
    tasks = TaskStore(projects)
    task = tasks.create_task(handle.project_id, task_input())
    context = MinimalContextBuilder(projects).build(handle.project_id, task.task_id)
    output = SchemaEnforcedOutputValidator(projects).enforce(
        handle.project_id, task.task_id, response(value)
    )
    run = AgentRunStore(projects).create_run(output)
    ContextManifestStore(projects).create_manifest(run, context)
    artifact = ResultDraftArtifactStore(projects).create_artifact(
        handle.project_id, run.run_id, created_timestamp=CREATED_AT
    )
    return task, artifact


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="ruav-v7-7-") as temp:
        projects = ProjectStore(Path(temp) / "projects")
        handle = projects.create_project()

        task, artifact = create_artifact(projects, handle, calculation())
        validator = DeterministicThrustToWeightValidator(projects)
        valid = validator.validate(
            handle.project_id, artifact.record.artifact_id, artifact.record.version
        )
        if valid.overall is not ValidationStatus.PASS:
            raise SystemExit("valid thrust-to-weight did not PASS")
        details = valid.checks[0].to_dict()["details"]
        if details["computed_weight_n"] != 34.323275:
            raise SystemExit("weight evidence mismatch")
        if details["computed_thrust_to_weight"] != 2:
            raise SystemExit("ratio evidence mismatch")

        _task2, mismatch_artifact = create_artifact(
            projects, handle, calculation(reported=2.1)
        )
        mismatch = validator.validate(
            handle.project_id, mismatch_artifact.record.artifact_id, 1
        )
        if mismatch.overall is not ValidationStatus.FAIL:
            raise SystemExit("mismatched thrust-to-weight was not rejected")

        reopened = ArtifactVersionStore(projects).open_version(
            handle.project_id, artifact.record.artifact_id, 1
        )
        if reopened.status is not ArtifactState.DRAFT or reopened.validation_result is not None:
            raise SystemExit("V7.7 changed artifact lifecycle/persisted validation")

        tasks = TaskStore(projects)
        before = tasks.open_task(handle.project_id, task.task_id)

        restarted = ProjectStore(projects.projects_root)
        again = DeterministicThrustToWeightValidator(restarted).validate(
            handle.project_id, artifact.record.artifact_id, 1
        )
        if again != valid:
            raise SystemExit("restart changed thrust-to-weight result")
        if TaskStore(restarted).open_task(handle.project_id, task.task_id) != before:
            raise SystemExit("V7.7 changed task state")

        print(
            "THRUST_TO_WEIGHT=PASS "
            f"artifact={artifact.record.artifact_id.value}@v1 "
            "check=thrust_to_weight status=PASS thrust_n=68.64655 mass_kg=3.5 "
            "gravity_m_s2=9.80665 weight_n=34.323275 ratio=2 mismatch=FAIL "
            "restart=PASS artifact_state=DRAFT persisted_validation=none "
            "task_state=unchanged provider_calls=none "
            "mission_distance=reserved_for_V7_8 lifecycle_transition=reserved cp7=pending"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
