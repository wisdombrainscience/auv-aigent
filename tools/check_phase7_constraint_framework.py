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
from ai.validators import DeterministicConstraintFrameworkValidator

CREATED_AT = "2026-10-05T22:00:00Z"
VALIDATORS = (
    "schema", "finite_values", "units", "mass_budget", "battery_energy",
    "reserve", "thrust_to_weight", "mission_distance", "constraints",
)


def task_input():
    return TaskCreateInput(
        title="Acceptance constraint framework",
        role="uav_systems_engineer",
        priority=100,
        dependencies=(),
        input_artifacts=(),
        output_schema=AGENT_RESULT_OUTPUT_SCHEMA_ID,
        validators=VALIDATORS,
        review_required=False,
        max_attempts=3,
    )


def calculations(*, tw_required=1.5, battery_reported=99.9):
    return [
        {
            "kind": "mass_budget",
            "components": [
                {"name": "airframe", "mass_kg": 1.8},
                {"name": "battery", "mass_kg": 1.1},
                {"name": "payload", "mass_kg": 0.6},
            ],
            "reported_total_kg": 3.5,
            "limit_kg": 4.0,
        },
        {
            "kind": "battery_energy",
            "voltage_v": 22.2,
            "capacity_ah": 4.5,
            "reported_energy_wh": battery_reported,
        },
        {
            "kind": "reserve",
            "reserve_percent": 20,
            "reported_reserve_wh": 19.98,
            "reported_usable_energy_wh": 79.92,
        },
        {
            "kind": "thrust_to_weight",
            "total_thrust_n": 68.64655,
            "mass_kg": 3.5,
            "gravity_m_s2": 9.80665,
            "reported_thrust_to_weight": 2,
        },
        {
            "kind": "mission_distance",
            "frame": "LOCAL_NED",
            "points_m": [
                [0, 0, 0], [0, 0, -3], [3, 4, -3],
                [6, 8, -3], [0, 0, -3], [0, 0, 0],
            ],
            "reported_distance_m": 26,
        },
        {
            "kind": "constraints",
            "constraints": [
                {
                    "constraint_id": "mass.max",
                    "field": "mass.total_kg",
                    "source_kind": "mass_budget",
                    "source_field": "reported_total_kg",
                    "operator": "<=",
                    "required_value": 4,
                    "unit": "kg",
                },
                {
                    "constraint_id": "energy.min",
                    "field": "battery.energy_wh",
                    "source_kind": "battery_energy",
                    "source_field": "reported_energy_wh",
                    "operator": ">=",
                    "required_value": 90,
                    "unit": "Wh",
                },
                {
                    "constraint_id": "reserve.min",
                    "field": "battery.reserve_percent",
                    "source_kind": "reserve",
                    "source_field": "reserve_percent",
                    "operator": ">=",
                    "required_value": 20,
                    "unit": "%",
                },
                {
                    "constraint_id": "twr.min",
                    "field": "propulsion.thrust_to_weight",
                    "source_kind": "thrust_to_weight",
                    "source_field": "reported_thrust_to_weight",
                    "operator": ">=",
                    "required_value": tw_required,
                    "unit": "1",
                },
                {
                    "constraint_id": "distance.max",
                    "field": "mission.distance_m",
                    "source_kind": "mission_distance",
                    "source_field": "reported_distance_m",
                    "operator": "<=",
                    "required_value": 30,
                    "unit": "m",
                },
            ],
        },
    ]


def response(values):
    payload = {
        "status": "PROPOSED",
        "claims": [{"classification": "CALCULATED_VALUE", "statement": "Acceptance constraints."}],
        "assumptions": [],
        "calculations": values,
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


def create_artifact(projects, handle, values, *, timestamp):
    tasks = TaskStore(projects)
    task = tasks.create_task(handle.project_id, task_input())
    context = MinimalContextBuilder(projects).build(handle.project_id, task.task_id)
    output = SchemaEnforcedOutputValidator(projects).enforce(
        handle.project_id, task.task_id, response(values)
    )
    run = AgentRunStore(projects).create_run(output)
    ContextManifestStore(projects).create_manifest(run, context)
    artifact = ResultDraftArtifactStore(projects).create_artifact(
        handle.project_id, run.run_id, created_timestamp=timestamp
    )
    return task, artifact


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="ruav-v7-9-") as temp:
        projects = ProjectStore(Path(temp) / "projects")
        handle = projects.create_project()
        validator = DeterministicConstraintFrameworkValidator(projects)

        task, artifact = create_artifact(
            projects, handle, calculations(), timestamp=CREATED_AT
        )
        valid = validator.validate(handle.project_id, artifact.record.artifact_id, 1)
        if valid.overall is not ValidationStatus.PASS or len(valid.checks) != 5:
            raise SystemExit("valid constraint set did not PASS with five checks")
        details = valid.checks[-1].to_dict()["details"]
        if details["observed_value"] != 26 or details["required_value"] != 30:
            raise SystemExit("constraint evidence mismatch")

        _task2, policy_artifact = create_artifact(
            projects, handle, calculations(tw_required=2.5), timestamp="2026-10-05T22:00:01Z"
        )
        policy = validator.validate(handle.project_id, policy_artifact.record.artifact_id, 1)
        if policy.overall is not ValidationStatus.FAIL:
            raise SystemExit("policy violation was not rejected")

        _task3, source_artifact = create_artifact(
            projects, handle, calculations(battery_reported=100), timestamp="2026-10-05T22:00:02Z"
        )
        source = validator.validate(handle.project_id, source_artifact.record.artifact_id, 1)
        if source.overall is not ValidationStatus.FAIL:
            raise SystemExit("invalid deterministic source was not rejected")

        reopened = ArtifactVersionStore(projects).open_version(
            handle.project_id, artifact.record.artifact_id, 1
        )
        if reopened.status is not ArtifactState.DRAFT or reopened.validation_result is not None:
            raise SystemExit("V7.9 changed artifact lifecycle/persisted validation")

        tasks = TaskStore(projects)
        before = tasks.open_task(handle.project_id, task.task_id)
        restarted = ProjectStore(projects.projects_root)
        again = DeterministicConstraintFrameworkValidator(restarted).validate(
            handle.project_id, artifact.record.artifact_id, 1
        )
        if again != valid:
            raise SystemExit("restart changed constraint result")
        if TaskStore(restarted).open_task(handle.project_id, task.task_id) != before:
            raise SystemExit("V7.9 changed task state")

        print(
            "CONSTRAINT_FRAMEWORK=PASS "
            f"artifact={artifact.record.artifact_id.value}@v1 "
            "check=constraints status=PASS constraints=5 sources=5 "
            "policy_violation=FAIL source_invalid=FAIL restart=PASS "
            "artifact_state=DRAFT persisted_validation=none task_state=unchanged provider_calls=none "
            "validation_issue_schema=reserved_for_V7_10 lifecycle_transition=reserved cp7=pending"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
