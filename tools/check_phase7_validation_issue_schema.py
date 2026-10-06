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

from ai.agents import (
    AGENT_RESULT_OUTPUT_SCHEMA_ID,
    AgentRunStore,
    ContextManifestStore,
    MinimalContextBuilder,
    ResultDraftArtifactStore,
    SchemaEnforcedOutputValidator,
)
from ai.artifacts import ArtifactVersionStore
from ai.contracts import ValidationStatus, normalize_validation_issues
from ai.contracts.artifact_states import ArtifactState
from ai.project.store import ProjectStore
from ai.providers.contracts import GenerateResponse
from ai.tasks import TaskCreateInput, TaskStore
from ai.validators import DeterministicConstraintFrameworkValidator

CREATED_AT = "2026-10-05T23:00:00Z"
VALIDATORS = (
    "schema", "finite_values", "units", "mass_budget", "battery_energy",
    "reserve", "thrust_to_weight", "mission_distance", "constraints",
)


def task_input():
    return TaskCreateInput(
        title="Acceptance validation issue schema",
        role="uav_systems_engineer",
        priority=100,
        dependencies=(),
        input_artifacts=(),
        output_schema=AGENT_RESULT_OUTPUT_SCHEMA_ID,
        validators=VALIDATORS,
        review_required=False,
        max_attempts=3,
    )


def calculations(*, tw_required: float):
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
            "reported_energy_wh": 99.9,
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
                    "constraint_id": "twr.min",
                    "field": "propulsion.thrust_to_weight",
                    "source_kind": "thrust_to_weight",
                    "source_field": "reported_thrust_to_weight",
                    "operator": ">=",
                    "required_value": tw_required,
                    "unit": "1",
                }
            ],
        },
    ]


def response(values):
    payload = {
        "status": "PROPOSED",
        "claims": [
            {
                "classification": "CALCULATED_VALUE",
                "statement": "Validation issue schema acceptance proposal.",
            }
        ],
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


def issue_sequence(projects: ProjectStore) -> int:
    with sqlite3.connect(projects.database_path) as conn:
        row = conn.execute(
            "SELECT last_sequence FROM canonical_id_sequences WHERE kind='issue'"
        ).fetchone()
    if row is None or not isinstance(row[0], int):
        raise SystemExit("ISSUE canonical sequence row missing")
    return row[0]


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="ruav-v7-10-") as temp:
        projects = ProjectStore(Path(temp) / "projects")
        handle = projects.create_project()
        validator = DeterministicConstraintFrameworkValidator(projects)

        pass_task, pass_artifact = create_artifact(
            projects,
            handle,
            calculations(tw_required=1.5),
            timestamp=CREATED_AT,
        )
        passing = validator.validate(
            handle.project_id, pass_artifact.record.artifact_id, 1
        )
        if passing.overall is not ValidationStatus.PASS:
            raise SystemExit("passing constraint result did not PASS")
        if normalize_validation_issues(passing).issues:
            raise SystemExit("PASS ValidationResult emitted validation issues")

        fail_task, fail_artifact = create_artifact(
            projects,
            handle,
            calculations(tw_required=2.5),
            timestamp="2026-10-05T23:00:01Z",
        )
        before_task = TaskStore(projects).open_task(handle.project_id, fail_task.task_id)
        before_issue_sequence = issue_sequence(projects)
        failing = validator.validate(
            handle.project_id, fail_artifact.record.artifact_id, 1
        )
        if failing.overall is not ValidationStatus.FAIL:
            raise SystemExit("policy violation did not produce FAIL ValidationResult")

        issue_set = normalize_validation_issues(failing)
        if len(issue_set.issues) != 1:
            raise SystemExit("expected exactly one normalized validation issue")
        issue = issue_set.issues[0]
        if issue.check_id != "constraint.twr.min":
            raise SystemExit("validation issue check binding mismatch")
        if issue.field != "propulsion.thrust_to_weight":
            raise SystemExit("validation issue field binding mismatch")
        details = issue.to_dict()["details"]
        if details.get("observed_value") != 2 or details.get("required_value") != 2.5:
            raise SystemExit("validation issue evidence was not preserved")
        if details.get("unit") != "1" or details.get("satisfied") is not False:
            raise SystemExit("validation issue evidence semantics mismatch")
        if not issue.fingerprint.startswith("sha256:") or len(issue.fingerprint) != 71:
            raise SystemExit("validation issue fingerprint is not canonical sha256")

        reopened = ArtifactVersionStore(projects).open_version(
            handle.project_id, fail_artifact.record.artifact_id, 1
        )
        if reopened.status is not ArtifactState.DRAFT or reopened.validation_result is not None:
            raise SystemExit("V7.10 changed artifact lifecycle/persisted validation")
        if TaskStore(projects).open_task(handle.project_id, fail_task.task_id) != before_task:
            raise SystemExit("V7.10 changed task state")
        if issue_sequence(projects) != before_issue_sequence:
            raise SystemExit("V7.10 allocated canonical ISSUE IDs")

        restarted = ProjectStore(projects.projects_root)
        again_result = DeterministicConstraintFrameworkValidator(restarted).validate(
            handle.project_id, fail_artifact.record.artifact_id, 1
        )
        again_issues = normalize_validation_issues(again_result)
        if again_issues != issue_set:
            raise SystemExit("restart changed normalized validation issue")
        if issue_sequence(restarted) != before_issue_sequence:
            raise SystemExit("restart path allocated canonical ISSUE IDs")

        print(
            "VALIDATION_ISSUE_SCHEMA=PASS "
            f"artifact={fail_artifact.record.artifact_id.value}@v1 "
            "source=constraints validation_status=FAIL issues=1 "
            "fingerprint=PASS evidence=PASS pass_result_empty=PASS restart=PASS "
            "issue_id=reserved persistence=none artifact_state=DRAFT "
            "persisted_validation=none task_state=unchanged provider_calls=none "
            "lifecycle_transition=reserved cp7=READY"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
