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
    BATTERY_ENERGY_CALCULATION_KIND,
    BATTERY_ENERGY_VALIDATOR_CONTRACT_VERSION,
    BATTERY_ENERGY_VALIDATOR_ID,
    MAX_BATTERY_ENERGY_VALIDATION_BYTES,
    BatteryEnergyValidationConflictError,
    DeterministicBatteryEnergyValidator,
)


CREATED_AT = "2026-10-05T15:00:00Z"


def task_input(*, validators=("schema", "finite_values", "units", "battery_energy")):
    return TaskCreateInput(
        title="Validate deterministic battery energy",
        role="uav_systems_engineer",
        priority=100,
        dependencies=(),
        input_artifacts=(),
        output_schema=AGENT_RESULT_OUTPUT_SCHEMA_ID,
        validators=tuple(validators),
        review_required=False,
        max_attempts=3,
    )


def energy(*, voltage_v=22.2, capacity_ah=4.5, reported_energy_wh=99.9, extra=None):
    value = {
        "kind": "battery_energy",
        "voltage_v": voltage_v,
        "capacity_ah": capacity_ah,
        "reported_energy_wh": reported_energy_wh,
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
                "statement": "Deterministic battery energy proposal.",
            }
        ],
        "assumptions": [],
        "calculations": calculations if calculations is not None else [energy()],
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


def validate(tmp_path: Path, battery_energy=None, *, calculations=None):
    if calculations is None:
        calculations = [battery_energy if battery_energy is not None else energy()]
    projects, handle, task, artifact = setup_valid(tmp_path, calculations=calculations)
    result = DeterministicBatteryEnergyValidator(projects).validate(
        handle.project_id, artifact.record.artifact_id, artifact.record.version
    )
    return projects, handle, task, artifact, result


def test_contract_constants_are_explicit():
    assert BATTERY_ENERGY_VALIDATOR_CONTRACT_VERSION == "1.0"
    assert BATTERY_ENERGY_VALIDATOR_ID == "battery_energy"
    assert BATTERY_ENERGY_CALCULATION_KIND == "battery_energy"
    assert MAX_BATTERY_ENERGY_VALIDATION_BYTES == 10 * 1024 * 1024


def test_valid_energy_passes_with_deterministic_evidence(tmp_path):
    _projects, _handle, _task, _artifact, result = validate(tmp_path)
    assert result.overall is ValidationStatus.PASS
    check = result.checks[0]
    assert check.check_id == "battery_energy"
    details = check.to_dict()["details"]
    assert details["formula"] == "Wh=V*Ah"
    assert details["voltage_v"] == 22.2
    assert details["capacity_ah"] == 4.5
    assert details["computed_energy_wh"] == 99.9
    assert details["reported_energy_wh"] == 99.9
    assert details["reconciled"] is True
    assert details["voltage_unit"] == "V"
    assert details["capacity_unit"] == "Ah"
    assert details["energy_unit"] == "Wh"


def test_reported_energy_mismatch_is_fail(tmp_path):
    _p, _h, _t, _a, result = validate(
        tmp_path, energy(reported_energy_wh=100.0)
    )
    assert result.overall is ValidationStatus.FAIL
    assert "does not equal deterministic V * Ah product" in (result.checks[0].reason or "")
    assert result.checks[0].to_dict()["details"]["reconciled"] is False


def test_decimal_bookkeeping_avoids_binary_float_drift(tmp_path):
    _p, _h, _t, _a, result = validate(
        tmp_path,
        energy(voltage_v=0.1, capacity_ah=3, reported_energy_wh=0.3),
    )
    assert result.overall is ValidationStatus.PASS
    assert result.checks[0].to_dict()["details"]["computed_energy_wh"] == 0.3


def test_zero_capacity_is_bookkeeping_valid(tmp_path):
    _p, _h, _t, _a, result = validate(
        tmp_path,
        energy(voltage_v=22.2, capacity_ah=0, reported_energy_wh=0),
    )
    assert result.overall is ValidationStatus.PASS


def test_zero_voltage_is_bookkeeping_valid(tmp_path):
    _p, _h, _t, _a, result = validate(
        tmp_path,
        energy(voltage_v=0, capacity_ah=4.5, reported_energy_wh=0),
    )
    assert result.overall is ValidationStatus.PASS


@pytest.mark.parametrize("field", ["voltage_v", "capacity_ah", "reported_energy_wh"])
def test_energy_fields_must_be_nonnegative_numbers(tmp_path, field):
    kwargs = {field: -1}
    _p, _h, _t, _a, result = validate(tmp_path, energy(**kwargs))
    assert result.overall is ValidationStatus.FAIL
    assert "must be non-negative" in (result.checks[0].reason or "")


@pytest.mark.parametrize("bad", [True, "22.2", None, [], {}])
def test_voltage_must_be_numeric_not_bool_or_other_types(tmp_path, bad):
    _p, _h, _t, _a, result = validate(tmp_path, energy(voltage_v=bad))
    assert result.overall is ValidationStatus.FAIL
    assert "must be a number" in (result.checks[0].reason or "")


def test_energy_keys_are_exact(tmp_path):
    _p, _h, _t, _a, result = validate(tmp_path, energy(extra={"cells": 6}))
    assert result.overall is ValidationStatus.FAIL
    assert "must contain exactly" in (result.checks[0].reason or "")


def test_missing_battery_energy_calculation_fails(tmp_path):
    _p, _h, _t, _a, result = validate(
        tmp_path, calculations=[{"kind": "mass_budget", "reported_total_kg": 1}]
    )
    assert result.overall is ValidationStatus.FAIL
    assert "must contain one battery_energy" in (result.checks[0].reason or "")


def test_multiple_battery_energy_calculations_fail(tmp_path):
    _p, _h, _t, _a, result = validate(tmp_path, calculations=[energy(), energy()])
    assert result.overall is ValidationStatus.FAIL
    assert "exactly one battery_energy" in (result.checks[0].reason or "")


def test_unrelated_calculations_are_ignored(tmp_path):
    _p, _h, _t, _a, result = validate(
        tmp_path,
        calculations=[
            {"kind": "note", "value": 1},
            energy(),
            {"kind": "other", "value": 2},
        ],
    )
    assert result.overall is ValidationStatus.PASS


def test_task_must_request_battery_energy_validator(tmp_path):
    projects, handle, _task, artifact = setup_valid(
        tmp_path,
        validators=("schema", "finite_values", "units"),
    )
    with pytest.raises(BatteryEnergyValidationConflictError, match="did not request"):
        DeterministicBatteryEnergyValidator(projects).validate(
            handle.project_id, artifact.record.artifact_id, 1
        )


def test_missing_artifact_is_conflict_not_engineering_fail(tmp_path):
    projects = ProjectStore(tmp_path / "projects")
    handle = projects.create_project()
    with pytest.raises(BatteryEnergyValidationConflictError, match="opened safely"):
        DeterministicBatteryEnergyValidator(projects).validate(
            handle.project_id, "ART-000001", 1
        )


def test_tampered_blob_is_conflict_not_engineering_fail(tmp_path):
    projects, handle, _task, artifact = setup_valid(tmp_path)
    blob = ArtifactContentStore(projects).blob_path(handle.project_id, artifact.record.hash)
    blob.write_bytes(blob.read_bytes() + b"tamper")
    with pytest.raises(BatteryEnergyValidationConflictError, match="opened safely"):
        DeterministicBatteryEnergyValidator(projects).validate(
            handle.project_id, artifact.record.artifact_id, 1
        )


def test_validation_does_not_change_draft_lifecycle(tmp_path):
    projects, handle, _task, artifact = setup_valid(tmp_path)
    DeterministicBatteryEnergyValidator(projects).validate(
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
    DeterministicBatteryEnergyValidator(projects).validate(
        handle.project_id, artifact.record.artifact_id, 1
    )
    assert tasks.open_task(handle.project_id, task.task_id) == before


def test_restart_is_deterministic(tmp_path):
    projects, handle, _task, artifact = setup_valid(tmp_path)
    first = DeterministicBatteryEnergyValidator(projects).validate(
        handle.project_id, artifact.record.artifact_id, 1
    )
    restarted = ProjectStore(projects.projects_root)
    second = DeterministicBatteryEnergyValidator(restarted).validate(
        handle.project_id, artifact.record.artifact_id, 1
    )
    assert second == first
