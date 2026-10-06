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
    CONSTRAINT_CALCULATION_KIND,
    CONSTRAINT_FRAMEWORK_CONTRACT_VERSION,
    CONSTRAINT_FRAMEWORK_VALIDATOR_ID,
    CONSTRAINT_OPERATORS,
    MAX_CONSTRAINT_FIELD_CHARS,
    MAX_CONSTRAINT_ID_CHARS,
    MAX_CONSTRAINT_VALIDATION_BYTES,
    MAX_CONSTRAINTS,
    SUPPORTED_CONSTRAINT_SOURCES,
    ConstraintFrameworkValidationConflictError,
    DeterministicConstraintFrameworkValidator,
)


CREATED_AT = "2026-10-05T21:00:00Z"
DEFAULT_VALIDATORS = (
    "schema",
    "finite_values",
    "units",
    "mass_budget",
    "battery_energy",
    "reserve",
    "thrust_to_weight",
    "mission_distance",
    "constraints",
)


def task_input(*, validators=DEFAULT_VALIDATORS):
    return TaskCreateInput(
        title="Evaluate deterministic engineering constraints",
        role="uav_systems_engineer",
        priority=100,
        dependencies=(),
        input_artifacts=(),
        output_schema=AGENT_RESULT_OUTPUT_SCHEMA_ID,
        validators=tuple(validators),
        review_required=False,
        max_attempts=3,
    )


def mass_budget(*, reported_total_kg=3.5, limit_kg=4.0):
    return {
        "kind": "mass_budget",
        "components": [
            {"name": "airframe", "mass_kg": 1.8},
            {"name": "battery", "mass_kg": 1.1},
            {"name": "payload", "mass_kg": 0.6},
        ],
        "reported_total_kg": reported_total_kg,
        "limit_kg": limit_kg,
    }


def battery_energy(*, reported_energy_wh=99.9):
    return {
        "kind": "battery_energy",
        "voltage_v": 22.2,
        "capacity_ah": 4.5,
        "reported_energy_wh": reported_energy_wh,
    }


def reserve(*, reserve_percent=20, reported_reserve_wh=19.98, reported_usable_energy_wh=79.92):
    return {
        "kind": "reserve",
        "reserve_percent": reserve_percent,
        "reported_reserve_wh": reported_reserve_wh,
        "reported_usable_energy_wh": reported_usable_energy_wh,
    }


def thrust_to_weight(*, reported_thrust_to_weight=2):
    return {
        "kind": "thrust_to_weight",
        "total_thrust_n": 68.64655,
        "mass_kg": 3.5,
        "gravity_m_s2": 9.80665,
        "reported_thrust_to_weight": reported_thrust_to_weight,
    }


def mission_distance(*, reported_distance_m=26):
    return {
        "kind": "mission_distance",
        "frame": "LOCAL_NED",
        "points_m": [
            [0, 0, 0],
            [0, 0, -3],
            [3, 4, -3],
            [6, 8, -3],
            [0, 0, -3],
            [0, 0, 0],
        ],
        "reported_distance_m": reported_distance_m,
    }


def constraint(
    constraint_id="mass.max",
    *,
    field="mass.total_kg",
    source_kind="mass_budget",
    source_field="reported_total_kg",
    operator="<=",
    required_value=4,
    unit="kg",
    extra=None,
):
    value = {
        "constraint_id": constraint_id,
        "field": field,
        "source_kind": source_kind,
        "source_field": source_field,
        "operator": operator,
        "required_value": required_value,
        "unit": unit,
    }
    if extra:
        value.update(extra)
    return value


def constraint_set(*, constraints=None, extra=None):
    value = {
        "kind": "constraints",
        "constraints": constraints if constraints is not None else [constraint()],
    }
    if extra:
        value.update(extra)
    return value


def all_calculations(*, constraints=None, battery_override=None):
    return [
        mass_budget(),
        battery_override if battery_override is not None else battery_energy(),
        reserve(),
        thrust_to_weight(),
        mission_distance(),
        constraint_set(constraints=constraints),
    ]


def response(*, calculations=None):
    payload = {
        "status": "PROPOSED",
        "claims": [
            {
                "classification": "CALCULATED_VALUE",
                "statement": "Deterministic constraint evaluation proposal.",
            }
        ],
        "assumptions": [],
        "calculations": calculations if calculations is not None else all_calculations(),
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


def setup_valid(tmp_path: Path, *, calculations=None, validators=DEFAULT_VALIDATORS):
    projects = ProjectStore(tmp_path / "remote-uav" / "projects")
    handle = projects.create_project()
    tasks = TaskStore(projects)
    task = tasks.create_task(handle.project_id, task_input(validators=validators))
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


def validate(tmp_path: Path, *, constraints=None, calculations=None, validators=DEFAULT_VALIDATORS):
    if calculations is None:
        calculations = all_calculations(constraints=constraints)
    projects, handle, task, artifact = setup_valid(
        tmp_path, calculations=calculations, validators=validators
    )
    result = DeterministicConstraintFrameworkValidator(projects).validate(
        handle.project_id, artifact.record.artifact_id, artifact.record.version
    )
    return projects, handle, task, artifact, result


def test_contract_constants_are_explicit():
    assert CONSTRAINT_FRAMEWORK_CONTRACT_VERSION == "1.0"
    assert CONSTRAINT_FRAMEWORK_VALIDATOR_ID == "constraints"
    assert CONSTRAINT_CALCULATION_KIND == "constraints"
    assert MAX_CONSTRAINT_VALIDATION_BYTES == 10 * 1024 * 1024
    assert MAX_CONSTRAINTS == 64
    assert MAX_CONSTRAINT_ID_CHARS == 64
    assert MAX_CONSTRAINT_FIELD_CHARS == 128
    assert CONSTRAINT_OPERATORS == ("<", "<=", "==", "!=", ">=", ">")
    assert SUPPORTED_CONSTRAINT_SOURCES == (
        "battery_energy", "mass_budget", "mission_distance", "reserve", "thrust_to_weight"
    )


def test_one_passing_constraint_returns_one_c1_7_check(tmp_path):
    _p, _h, _t, _a, result = validate(tmp_path)
    assert result.overall is ValidationStatus.PASS
    assert len(result.checks) == 1
    check = result.checks[0]
    assert check.check_id == "constraint.mass.max"
    assert check.field == "mass.total_kg"
    details = check.to_dict()["details"]
    assert details["source_validator"] == "mass_budget"
    assert details["source_validation_status"] == "PASS"
    assert details["observed_value"] == 3.5
    assert details["required_value"] == 4
    assert details["unit"] == "kg"
    assert details["satisfied"] is True


def test_multiple_domain_constraints_are_evaluated_in_declared_order(tmp_path):
    constraints = [
        constraint(),
        constraint(
            "energy.min",
            field="battery.energy_wh",
            source_kind="battery_energy",
            source_field="reported_energy_wh",
            operator=">=",
            required_value=90,
            unit="Wh",
        ),
        constraint(
            "reserve.min",
            field="battery.reserve_percent",
            source_kind="reserve",
            source_field="reserve_percent",
            operator=">=",
            required_value=20,
            unit="%",
        ),
        constraint(
            "twr.min",
            field="propulsion.thrust_to_weight",
            source_kind="thrust_to_weight",
            source_field="reported_thrust_to_weight",
            operator=">=",
            required_value=1.5,
            unit="1",
        ),
        constraint(
            "distance.max",
            field="mission.distance_m",
            source_kind="mission_distance",
            source_field="reported_distance_m",
            operator="<=",
            required_value=30,
            unit="m",
        ),
    ]
    _p, _h, _t, _a, result = validate(tmp_path, constraints=constraints)
    assert result.overall is ValidationStatus.PASS
    assert [c.check_id for c in result.checks] == [
        "constraint.mass.max",
        "constraint.energy.min",
        "constraint.reserve.min",
        "constraint.twr.min",
        "constraint.distance.max",
    ]


@pytest.mark.parametrize(
    ("operator", "required", "expected"),
    [
        ("<", 4, True),
        ("<=", 3.5, True),
        ("==", 3.5, True),
        ("!=", 4, True),
        (">=", 3.5, True),
        (">", 3, True),
        ("<", 3, False),
    ],
)
def test_supported_operators_are_deterministic(tmp_path, operator, required, expected):
    _p, _h, _t, _a, result = validate(
        tmp_path,
        constraints=[constraint(operator=operator, required_value=required)],
    )
    assert (result.overall is ValidationStatus.PASS) is expected
    assert result.checks[0].to_dict()["details"]["satisfied"] is expected


def test_failed_policy_constraint_is_fail_not_conflict(tmp_path):
    _p, _h, _t, _a, result = validate(
        tmp_path,
        constraints=[
            constraint(
                "twr.min",
                field="propulsion.thrust_to_weight",
                source_kind="thrust_to_weight",
                source_field="reported_thrust_to_weight",
                operator=">=",
                required_value=2.5,
                unit="1",
            )
        ],
    )
    assert result.overall is ValidationStatus.FAIL
    assert "observed_value >= required_value is false" in (result.checks[0].reason or "")


def test_negative_thresholds_are_allowed_for_generic_numeric_constraints(tmp_path):
    _p, _h, _t, _a, result = validate(
        tmp_path,
        constraints=[constraint(operator=">", required_value=-1)],
    )
    assert result.overall is ValidationStatus.PASS


def test_source_validator_must_pass_before_constraint_is_evaluated(tmp_path):
    calculations = all_calculations(
        constraints=[
            constraint(
                "energy.min",
                field="battery.energy_wh",
                source_kind="battery_energy",
                source_field="reported_energy_wh",
                operator=">=",
                required_value=90,
                unit="Wh",
            )
        ],
        battery_override=battery_energy(reported_energy_wh=100),
    )
    _p, _h, _t, _a, result = validate(tmp_path, calculations=calculations)
    assert result.overall is ValidationStatus.FAIL
    assert "source validator battery_energy did not PASS" in (result.checks[0].reason or "")
    assert result.checks[0].to_dict()["details"]["source_validation_status"] == "FAIL"


def test_missing_source_validator_request_is_configuration_conflict(tmp_path):
    validators = tuple(v for v in DEFAULT_VALIDATORS if v != "mass_budget")
    projects, handle, _task, artifact = setup_valid(tmp_path, validators=validators)
    with pytest.raises(ConstraintFrameworkValidationConflictError, match="source validator mass_budget"):
        DeterministicConstraintFrameworkValidator(projects).validate(
            handle.project_id, artifact.record.artifact_id, 1
        )


def test_unit_must_match_source_field_canonical_unit(tmp_path):
    _p, _h, _t, _a, result = validate(
        tmp_path, constraints=[constraint(unit="g")]
    )
    assert result.overall is ValidationStatus.FAIL
    assert "must be exactly kg" in (result.checks[0].reason or "")


def test_unsupported_source_kind_fails(tmp_path):
    _p, _h, _t, _a, result = validate(
        tmp_path, constraints=[constraint(source_kind="unknown")]
    )
    assert result.overall is ValidationStatus.FAIL
    assert "unsupported constraint source_kind" in (result.checks[0].reason or "")


def test_unsupported_source_field_fails(tmp_path):
    _p, _h, _t, _a, result = validate(
        tmp_path, constraints=[constraint(source_field="computed_total_kg")]
    )
    assert result.overall is ValidationStatus.FAIL
    assert "unsupported constraint source field" in (result.checks[0].reason or "")


def test_unknown_operator_fails(tmp_path):
    _p, _h, _t, _a, result = validate(
        tmp_path, constraints=[constraint(operator="~=")]
    )
    assert result.overall is ValidationStatus.FAIL
    assert "operator must be one of" in (result.checks[0].reason or "")


@pytest.mark.parametrize("bad", [True, "4", None, [], {}])
def test_required_value_must_be_numeric_not_bool(tmp_path, bad):
    _p, _h, _t, _a, result = validate(
        tmp_path, constraints=[constraint(required_value=bad)]
    )
    assert result.overall is ValidationStatus.FAIL
    assert "required_value must be a number" in (result.checks[0].reason or "")


def test_constraint_id_must_be_stable_ascii_token(tmp_path):
    _p, _h, _t, _a, result = validate(
        tmp_path, constraints=[constraint("bad id")]
    )
    assert result.overall is ValidationStatus.FAIL
    assert "ASCII letters" in (result.checks[0].reason or "")


def test_duplicate_constraint_id_fails(tmp_path):
    _p, _h, _t, _a, result = validate(
        tmp_path, constraints=[constraint("same"), constraint("same")]
    )
    assert result.overall is ValidationStatus.FAIL
    assert "duplicate constraint_id" in (result.checks[0].reason or "")


def test_constraint_keys_are_exact(tmp_path):
    _p, _h, _t, _a, result = validate(
        tmp_path, constraints=[constraint(extra={"severity": "HIGH"})]
    )
    assert result.overall is ValidationStatus.FAIL
    assert "must contain exactly" in (result.checks[0].reason or "")


def test_constraint_set_keys_are_exact(tmp_path):
    calculations = all_calculations()
    calculations[-1] = constraint_set(extra={"policy": "uav"})
    _p, _h, _t, _a, result = validate(tmp_path, calculations=calculations)
    assert result.overall is ValidationStatus.FAIL
    assert "constraints calculation must contain exactly" in (result.checks[0].reason or "")


def test_empty_constraint_set_fails(tmp_path):
    _p, _h, _t, _a, result = validate(tmp_path, constraints=[])
    assert result.overall is ValidationStatus.FAIL
    assert "must not be empty" in (result.checks[0].reason or "")


def test_constraint_count_is_bounded(tmp_path):
    constraints = [constraint(f"c{i}") for i in range(MAX_CONSTRAINTS + 1)]
    _p, _h, _t, _a, result = validate(tmp_path, constraints=constraints)
    assert result.overall is ValidationStatus.FAIL
    assert "MAX_CONSTRAINTS" in (result.checks[0].reason or "")


def test_missing_constraint_calculation_fails(tmp_path):
    calculations = all_calculations()[:-1]
    _p, _h, _t, _a, result = validate(tmp_path, calculations=calculations)
    assert result.overall is ValidationStatus.FAIL
    assert "must contain one constraints" in (result.checks[0].reason or "")


def test_multiple_constraint_calculations_fail(tmp_path):
    calculations = all_calculations() + [constraint_set()]
    _p, _h, _t, _a, result = validate(tmp_path, calculations=calculations)
    assert result.overall is ValidationStatus.FAIL
    assert "exactly one constraints" in (result.checks[0].reason or "")


def test_task_must_request_constraints_validator(tmp_path):
    validators = tuple(v for v in DEFAULT_VALIDATORS if v != "constraints")
    projects, handle, _task, artifact = setup_valid(tmp_path, validators=validators)
    with pytest.raises(ConstraintFrameworkValidationConflictError, match="did not request"):
        DeterministicConstraintFrameworkValidator(projects).validate(
            handle.project_id, artifact.record.artifact_id, 1
        )


def test_missing_artifact_is_conflict_not_engineering_fail(tmp_path):
    projects = ProjectStore(tmp_path / "projects")
    handle = projects.create_project()
    with pytest.raises(ConstraintFrameworkValidationConflictError, match="opened safely"):
        DeterministicConstraintFrameworkValidator(projects).validate(
            handle.project_id, "ART-000001", 1
        )


def test_tampered_blob_is_conflict_not_engineering_fail(tmp_path):
    projects, handle, _task, artifact = setup_valid(tmp_path)
    blob = ArtifactContentStore(projects).blob_path(handle.project_id, artifact.record.hash)
    blob.write_bytes(blob.read_bytes() + b"tamper")
    with pytest.raises(ConstraintFrameworkValidationConflictError, match="opened safely"):
        DeterministicConstraintFrameworkValidator(projects).validate(
            handle.project_id, artifact.record.artifact_id, 1
        )


def test_validation_does_not_change_draft_lifecycle(tmp_path):
    projects, handle, _task, artifact = setup_valid(tmp_path)
    DeterministicConstraintFrameworkValidator(projects).validate(
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
    DeterministicConstraintFrameworkValidator(projects).validate(
        handle.project_id, artifact.record.artifact_id, 1
    )
    assert tasks.open_task(handle.project_id, task.task_id) == before


def test_restart_is_deterministic(tmp_path):
    projects, handle, _task, artifact = setup_valid(tmp_path)
    first = DeterministicConstraintFrameworkValidator(projects).validate(
        handle.project_id, artifact.record.artifact_id, 1
    )
    restarted = ProjectStore(projects.projects_root)
    second = DeterministicConstraintFrameworkValidator(restarted).validate(
        handle.project_id, artifact.record.artifact_id, 1
    )
    assert second == first
