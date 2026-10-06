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
from ai.validators import DeterministicReserveValidator


CREATED_AT = "2026-10-05T16:30:00Z"


def task_input():
    return TaskCreateInput(
        title="Acceptance reserve calculation",
        role="uav_systems_engineer",
        priority=100,
        dependencies=(),
        input_artifacts=(),
        output_schema=AGENT_RESULT_OUTPUT_SCHEMA_ID,
        validators=("schema", "finite_values", "units", "battery_energy", "reserve"),
        review_required=False,
        max_attempts=3,
    )


def battery():
    return {
        "kind": "battery_energy",
        "voltage_v": 22.2,
        "capacity_ah": 4.5,
        "reported_energy_wh": 99.9,
    }


def reserve(*, reported_reserve_wh=19.98, reported_usable_energy_wh=79.92):
    return {
        "kind": "reserve",
        "reserve_percent": 20,
        "reported_reserve_wh": reported_reserve_wh,
        "reported_usable_energy_wh": reported_usable_energy_wh,
    }


def response(reserve_calc):
    payload = {
        "status": "PROPOSED",
        "claims": [
            {
                "classification": "CALCULATED_VALUE",
                "statement": "Acceptance reserve calculation.",
            }
        ],
        "assumptions": [],
        "calculations": [battery(), reserve_calc],
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


def create_artifact(projects, handle, reserve_calc):
    tasks = TaskStore(projects)
    task = tasks.create_task(handle.project_id, task_input())
    context = MinimalContextBuilder(projects).build(handle.project_id, task.task_id)
    output = SchemaEnforcedOutputValidator(projects).enforce(
        handle.project_id, task.task_id, response(reserve_calc)
    )
    run = AgentRunStore(projects).create_run(output)
    ContextManifestStore(projects).create_manifest(run, context)
    artifact = ResultDraftArtifactStore(projects).create_artifact(
        handle.project_id, run.run_id, created_timestamp=CREATED_AT
    )
    return task, artifact


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="ruav-v7-6-") as temp:
        projects = ProjectStore(Path(temp) / "projects")
        handle = projects.create_project()

        task, artifact = create_artifact(projects, handle, reserve())
        validator = DeterministicReserveValidator(projects)
        valid = validator.validate(
            handle.project_id, artifact.record.artifact_id, artifact.record.version
        )
        if valid.overall is not ValidationStatus.PASS:
            raise SystemExit("valid reserve calculation did not PASS")
        details = valid.checks[0].to_dict()["details"]
        if (
            details["battery_energy_wh"] != 99.9
            or details["reserve_percent"] != 20
            or details["computed_reserve_wh"] != 19.98
            or details["computed_usable_energy_wh"] != 79.92
        ):
            raise SystemExit("reserve evidence mismatch")

        _task2, reserve_mismatch_artifact = create_artifact(
            projects, handle, reserve(reported_reserve_wh=20)
        )
        mismatch = validator.validate(
            handle.project_id, reserve_mismatch_artifact.record.artifact_id, 1
        )
        if mismatch.overall is not ValidationStatus.FAIL:
            raise SystemExit("mismatched reserve energy was not rejected")

        _task3, usable_mismatch_artifact = create_artifact(
            projects, handle, reserve(reported_usable_energy_wh=80)
        )
        usable_mismatch = validator.validate(
            handle.project_id, usable_mismatch_artifact.record.artifact_id, 1
        )
        if usable_mismatch.overall is not ValidationStatus.FAIL:
            raise SystemExit("mismatched usable energy was not rejected")

        reopened = ArtifactVersionStore(projects).open_version(
            handle.project_id, artifact.record.artifact_id, 1
        )
        if reopened.status is not ArtifactState.DRAFT or reopened.validation_result is not None:
            raise SystemExit("V7.6 changed artifact lifecycle/persisted validation")

        tasks = TaskStore(projects)
        before = tasks.open_task(handle.project_id, task.task_id)

        restarted = ProjectStore(projects.projects_root)
        again = DeterministicReserveValidator(restarted).validate(
            handle.project_id, artifact.record.artifact_id, 1
        )
        if again != valid:
            raise SystemExit("restart changed reserve result")
        if TaskStore(restarted).open_task(handle.project_id, task.task_id) != before:
            raise SystemExit("V7.6 changed task state")

        print(
            "RESERVE_CALCULATION=PASS "
            f"artifact={artifact.record.artifact_id.value}@v1 "
            "check=reserve status=PASS battery_wh=99.9 reserve_percent=20 "
            "reserve_wh=19.98 usable_wh=79.92 reserve_mismatch=FAIL usable_mismatch=FAIL "
            "restart=PASS artifact_state=DRAFT persisted_validation=none task_state=unchanged "
            "provider_calls=none thrust_to_weight=reserved_for_V7_7 "
            "lifecycle_transition=reserved cp7=pending"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
