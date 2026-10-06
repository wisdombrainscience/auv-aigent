from __future__ import annotations

import json
from pathlib import Path

import pytest

from ai.agents import (
    AGENT_RESULT_OUTPUT_SCHEMA_ID,
    AgentRunStore,
    ContextManifestStore,
    MinimalContextBuilder,
    ResultDraftArtifactStore,
    SchemaEnforcedOutputValidator,
)
from ai.artifacts import ArtifactVersionStore
from ai.artifacts.hashing import ArtifactContentStore
from ai.contracts.artifact_states import ArtifactState
from ai.contracts.validation_result import ValidationStatus
from ai.project.store import ProjectStore
from ai.providers.contracts import GenerateResponse
from ai.tasks import TaskCreateInput, TaskStore
from ai.validators import (
    MAX_THRUST_TO_WEIGHT_VALIDATION_BYTES,
    THRUST_TO_WEIGHT_CALCULATION_KIND,
    THRUST_TO_WEIGHT_VALIDATOR_CONTRACT_VERSION,
    THRUST_TO_WEIGHT_VALIDATOR_ID,
    DeterministicThrustToWeightValidator,
    ThrustToWeightValidationConflictError,
)


CREATED_AT = "2026-10-05T18:00:00Z"


def task_input(*, validators=("schema", "finite_values", "units", "thrust_to_weight")):
    return TaskCreateInput(
        title="Validate deterministic thrust-to-weight",
        role="uav_systems_engineer",
        priority=100,
        dependencies=(),
        input_artifacts=(),
        output_schema=AGENT_RESULT_OUTPUT_SCHEMA_ID,
        validators=tuple(validators),
        review_required=False,
        max_attempts=3,
    )


def thrust_to_weight(
    *,
    total_thrust_n=68.64655,
    mass_kg=3.5,
    gravity_m_s2=9.80665,
    reported_thrust_to_weight=2,
    extra=None,
):
    value = {
        "kind": "thrust_to_weight",
        "total_thrust_n": total_thrust_n,
        "mass_kg": mass_kg,
        "gravity_m_s2": gravity_m_s2,
        "reported_thrust_to_weight": reported_thrust_to_weight,
    }
    if extra:
        value.update(extra)
    return value


def response(*, calculations=None):
    payload = {
        "status": "PROPOSED",
        "claims": [
            {
                "classification": "CALCULATED_VALUE",
                "statement": "Deterministic thrust-to-weight proposal.",
            }
        ],
        "assumptions": [],
        "calculations": calculations if calculations is not None else [thrust_to_weight()],
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


def setup_valid(tmp_path: Path, *, calculations=None, validators=None):
    projects = ProjectStore(tmp_path / "remote-uav" / "projects")
    handle = projects.create_project()
    tasks = TaskStore(projects)
    task = tasks.create_task(
        handle.project_id,
        task_input(**({"validators": validators} if validators is not None else {})),
    )
    context = MinimalContextBuilder(projects).build(handle.project_id, task.task_id)
    output = SchemaEnforcedOutputValidator(projects).enforce(
        handle.project_id,
        task.task_id,
        response(calculations=calculations),
    )
    run = AgentRunStore(projects).create_run(output)
    ContextManifestStore(projects).create_manifest(run, context)
    artifact = ResultDraftArtifactStore(projects).create_artifact(
        handle.project_id, run.run_id, created_timestamp=CREATED_AT
    )
    return projects, handle, task, artifact


def validate(tmp_path: Path, calculation=None, *, calculations=None):
    if calculations is None:
        calculations = [calculation if calculation is not None else thrust_to_weight()]
    projects, handle, task, artifact = setup_valid(tmp_path, calculations=calculations)
    result = DeterministicThrustToWeightValidator(projects).validate(
        handle.project_id, artifact.record.artifact_id, artifact.record.version
    )
    return projects, handle, task, artifact, result


def test_contract_constants_are_explicit():
    assert THRUST_TO_WEIGHT_VALIDATOR_CONTRACT_VERSION == "1.0"
    assert THRUST_TO_WEIGHT_VALIDATOR_ID == "thrust_to_weight"
    assert THRUST_TO_WEIGHT_CALCULATION_KIND == "thrust_to_weight"
    assert MAX_THRUST_TO_WEIGHT_VALIDATION_BYTES == 10 * 1024 * 1024


def test_valid_thrust_to_weight_passes_with_deterministic_evidence(tmp_path):
    _p, _h, _t, _a, result = validate(tmp_path)
    assert result.overall is ValidationStatus.PASS
    check = result.checks[0]
    assert check.check_id == "thrust_to_weight"
    details = check.to_dict()["details"]
    assert details["formula"] == "T/W=total_thrust_n/(mass_kg*gravity_m_s2)"
    assert details["total_thrust_n"] == 68.64655
    assert details["mass_kg"] == 3.5
    assert details["gravity_m_s2"] == 9.80665
    assert details["computed_weight_n"] == 34.323275
    assert details["computed_thrust_to_weight"] == 2
    assert details["reported_thrust_to_weight"] == 2
    assert details["reconciled"] is True
    assert details["thrust_unit"] == "N"
    assert details["mass_unit"] == "kg"
    assert details["gravity_unit"] == "m/s^2"
    assert details["ratio_unit"] == "1"


def test_reported_ratio_mismatch_is_fail(tmp_path):
    _p, _h, _t, _a, result = validate(
        tmp_path, thrust_to_weight(reported_thrust_to_weight=2.1)
    )
    assert result.overall is ValidationStatus.FAIL
    assert "does not reconcile" in (result.checks[0].reason or "")
    assert result.checks[0].to_dict()["details"]["reconciled"] is False


def test_decimal_cross_multiplication_avoids_binary_float_drift(tmp_path):
    _p, _h, _t, _a, result = validate(
        tmp_path,
        thrust_to_weight(
            total_thrust_n=0.06,
            mass_kg=0.2,
            gravity_m_s2=0.1,
            reported_thrust_to_weight=3,
        ),
    )
    assert result.overall is ValidationStatus.PASS


def test_zero_thrust_is_bookkeeping_valid(tmp_path):
    _p, _h, _t, _a, result = validate(
        tmp_path,
        thrust_to_weight(total_thrust_n=0, reported_thrust_to_weight=0),
    )
    assert result.overall is ValidationStatus.PASS


@pytest.mark.parametrize("field", ["total_thrust_n", "mass_kg", "gravity_m_s2", "reported_thrust_to_weight"])
def test_fields_must_be_nonnegative(tmp_path, field):
    kwargs = {field: -1}
    _p, _h, _t, _a, result = validate(tmp_path, thrust_to_weight(**kwargs))
    assert result.overall is ValidationStatus.FAIL
    assert "must be non-negative" in (result.checks[0].reason or "")


@pytest.mark.parametrize("bad", [True, "68.6", None, [], {}])
def test_thrust_must_be_numeric_not_bool(tmp_path, bad):
    _p, _h, _t, _a, result = validate(tmp_path, thrust_to_weight(total_thrust_n=bad))
    assert result.overall is ValidationStatus.FAIL
    assert "must be a number" in (result.checks[0].reason or "")


def test_zero_mass_fails(tmp_path):
    _p, _h, _t, _a, result = validate(tmp_path, thrust_to_weight(mass_kg=0))
    assert result.overall is ValidationStatus.FAIL
    assert "mass_kg must be greater than zero" in (result.checks[0].reason or "")


def test_zero_gravity_fails(tmp_path):
    _p, _h, _t, _a, result = validate(tmp_path, thrust_to_weight(gravity_m_s2=0))
    assert result.overall is ValidationStatus.FAIL
    assert "gravity_m_s2 must be greater than zero" in (result.checks[0].reason or "")


def test_calculation_keys_are_exact(tmp_path):
    _p, _h, _t, _a, result = validate(
        tmp_path, thrust_to_weight(extra={"motor_count": 4})
    )
    assert result.overall is ValidationStatus.FAIL
    assert "must contain exactly" in (result.checks[0].reason or "")


def test_missing_calculation_fails(tmp_path):
    _p, _h, _t, _a, result = validate(
        tmp_path, calculations=[{"kind": "mass_budget", "reported_total_kg": 3.5}]
    )
    assert result.overall is ValidationStatus.FAIL
    assert "must contain one thrust_to_weight" in (result.checks[0].reason or "")


def test_multiple_calculations_fail(tmp_path):
    _p, _h, _t, _a, result = validate(
        tmp_path, calculations=[thrust_to_weight(), thrust_to_weight()]
    )
    assert result.overall is ValidationStatus.FAIL
    assert "exactly one thrust_to_weight" in (result.checks[0].reason or "")


def test_unrelated_calculations_are_ignored(tmp_path):
    _p, _h, _t, _a, result = validate(
        tmp_path,
        calculations=[
            {"kind": "mass_budget", "reported_total_kg": 3.5},
            thrust_to_weight(),
            {"kind": "reserve", "reserve_percent": 20},
        ],
    )
    assert result.overall is ValidationStatus.PASS


def test_task_must_request_validator(tmp_path):
    projects, handle, _task, artifact = setup_valid(
        tmp_path, validators=("schema", "finite_values", "units")
    )
    with pytest.raises(ThrustToWeightValidationConflictError, match="did not request"):
        DeterministicThrustToWeightValidator(projects).validate(
            handle.project_id, artifact.record.artifact_id, 1
        )


def test_missing_artifact_is_conflict_not_engineering_fail(tmp_path):
    projects = ProjectStore(tmp_path / "projects")
    handle = projects.create_project()
    with pytest.raises(ThrustToWeightValidationConflictError, match="opened safely"):
        DeterministicThrustToWeightValidator(projects).validate(
            handle.project_id, "ART-000001", 1
        )


def test_tampered_blob_is_conflict_not_engineering_fail(tmp_path):
    projects, handle, _task, artifact = setup_valid(tmp_path)
    blob = ArtifactContentStore(projects).blob_path(handle.project_id, artifact.record.hash)
    blob.write_bytes(blob.read_bytes() + b"tamper")
    with pytest.raises(ThrustToWeightValidationConflictError, match="opened safely"):
        DeterministicThrustToWeightValidator(projects).validate(
            handle.project_id, artifact.record.artifact_id, 1
        )


def test_validation_does_not_change_draft_lifecycle(tmp_path):
    projects, handle, _task, artifact = setup_valid(tmp_path)
    DeterministicThrustToWeightValidator(projects).validate(
        handle.project_id, artifact.record.artifact_id, 1
    )
    reopened = ArtifactVersionStore(projects).open_version(
        handle.project_id, artifact.record.artifact_id, 1
    )
    assert reopened.status is ArtifactState.DRAFT
    assert reopened.validation_result is None


def test_validation_does_not_change_task_state(tmp_path):
    projects, handle, task, artifact = setup_valid(tmp_path)
    tasks = TaskStore(projects)
    before = tasks.open_task(handle.project_id, task.task_id)
    DeterministicThrustToWeightValidator(projects).validate(
        handle.project_id, artifact.record.artifact_id, 1
    )
    assert tasks.open_task(handle.project_id, task.task_id) == before


def test_restart_is_deterministic(tmp_path):
    projects, handle, _task, artifact = setup_valid(tmp_path)
    first = DeterministicThrustToWeightValidator(projects).validate(
        handle.project_id, artifact.record.artifact_id, 1
    )
    restarted = ProjectStore(projects.projects_root)
    second = DeterministicThrustToWeightValidator(restarted).validate(
        handle.project_id, artifact.record.artifact_id, 1
    )
    assert second == first
