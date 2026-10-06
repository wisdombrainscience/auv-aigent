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
    MAX_RESERVE_VALIDATION_BYTES,
    RESERVE_CALCULATION_KIND,
    RESERVE_VALIDATOR_CONTRACT_VERSION,
    RESERVE_VALIDATOR_ID,
    DeterministicReserveValidator,
    ReserveValidationConflictError,
)


CREATED_AT = "2026-10-05T16:00:00Z"


def task_input(*, validators=("schema", "finite_values", "units", "battery_energy", "reserve")):
    return TaskCreateInput(
        title="Validate deterministic battery reserve",
        role="uav_systems_engineer",
        priority=100,
        dependencies=(),
        input_artifacts=(),
        output_schema=AGENT_RESULT_OUTPUT_SCHEMA_ID,
        validators=tuple(validators),
        review_required=False,
        max_attempts=3,
    )


def battery(*, voltage_v=22.2, capacity_ah=4.5, reported_energy_wh=99.9, extra=None):
    value = {
        "kind": "battery_energy",
        "voltage_v": voltage_v,
        "capacity_ah": capacity_ah,
        "reported_energy_wh": reported_energy_wh,
    }
    if extra:
        value.update(extra)
    return value


def reserve(*, reserve_percent=20, reported_reserve_wh=19.98, reported_usable_energy_wh=79.92, extra=None):
    value = {
        "kind": "reserve",
        "reserve_percent": reserve_percent,
        "reported_reserve_wh": reported_reserve_wh,
        "reported_usable_energy_wh": reported_usable_energy_wh,
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
                "statement": "Deterministic reserve proposal.",
            }
        ],
        "assumptions": [],
        "calculations": calculations if calculations is not None else [battery(), reserve()],
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


def validate(tmp_path: Path, *, battery_calc=None, reserve_calc=None, calculations=None):
    if calculations is None:
        calculations = [battery_calc or battery(), reserve_calc or reserve()]
    projects, handle, task, artifact = setup_valid(tmp_path, calculations=calculations)
    result = DeterministicReserveValidator(projects).validate(
        handle.project_id, artifact.record.artifact_id, artifact.record.version
    )
    return projects, handle, task, artifact, result


def test_contract_constants_are_explicit():
    assert RESERVE_VALIDATOR_CONTRACT_VERSION == "1.0"
    assert RESERVE_VALIDATOR_ID == "reserve"
    assert RESERVE_CALCULATION_KIND == "reserve"
    assert MAX_RESERVE_VALIDATION_BYTES == 10 * 1024 * 1024


def test_valid_reserve_passes_with_deterministic_evidence(tmp_path):
    _p, _h, _t, _a, result = validate(tmp_path)
    assert result.overall is ValidationStatus.PASS
    check = result.checks[0]
    assert check.check_id == "reserve"
    details = check.to_dict()["details"]
    assert details["battery_energy_wh"] == 99.9
    assert details["reserve_percent"] == 20
    assert details["computed_reserve_wh"] == 19.98
    assert details["reported_reserve_wh"] == 19.98
    assert details["computed_usable_energy_wh"] == 79.92
    assert details["reported_usable_energy_wh"] == 79.92
    assert details["battery_energy_reconciled"] is True
    assert details["reserve_reconciled"] is True
    assert details["usable_reconciled"] is True
    assert details["energy_unit"] == "Wh"
    assert details["percent_unit"] == "%"


def test_reported_reserve_mismatch_is_fail(tmp_path):
    _p, _h, _t, _a, result = validate(
        tmp_path, reserve_calc=reserve(reported_reserve_wh=20)
    )
    assert result.overall is ValidationStatus.FAIL
    assert "reported_reserve_wh" in (result.checks[0].reason or "")
    details = result.checks[0].to_dict()["details"]
    assert details["reserve_reconciled"] is False


def test_reported_usable_energy_mismatch_is_fail(tmp_path):
    _p, _h, _t, _a, result = validate(
        tmp_path, reserve_calc=reserve(reported_usable_energy_wh=80)
    )
    assert result.overall is ValidationStatus.FAIL
    assert "reported_usable_energy_wh" in (result.checks[0].reason or "")
    details = result.checks[0].to_dict()["details"]
    assert details["reserve_reconciled"] is True
    assert details["usable_reconciled"] is False


def test_battery_energy_must_reconcile_before_reserve(tmp_path):
    _p, _h, _t, _a, result = validate(
        tmp_path, battery_calc=battery(reported_energy_wh=100)
    )
    assert result.overall is ValidationStatus.FAIL
    assert "battery_energy.reported_energy_wh" in (result.checks[0].reason or "")


def test_decimal_bookkeeping_avoids_binary_float_drift(tmp_path):
    _p, _h, _t, _a, result = validate(
        tmp_path,
        battery_calc=battery(voltage_v=0.1, capacity_ah=3, reported_energy_wh=0.3),
        reserve_calc=reserve(
            reserve_percent=10,
            reported_reserve_wh=0.03,
            reported_usable_energy_wh=0.27,
        ),
    )
    assert result.overall is ValidationStatus.PASS
    details = result.checks[0].to_dict()["details"]
    assert details["computed_reserve_wh"] == 0.03
    assert details["computed_usable_energy_wh"] == 0.27


def test_zero_percent_means_all_energy_usable(tmp_path):
    _p, _h, _t, _a, result = validate(
        tmp_path,
        reserve_calc=reserve(
            reserve_percent=0,
            reported_reserve_wh=0,
            reported_usable_energy_wh=99.9,
        ),
    )
    assert result.overall is ValidationStatus.PASS


def test_hundred_percent_means_no_usable_energy(tmp_path):
    _p, _h, _t, _a, result = validate(
        tmp_path,
        reserve_calc=reserve(
            reserve_percent=100,
            reported_reserve_wh=99.9,
            reported_usable_energy_wh=0,
        ),
    )
    assert result.overall is ValidationStatus.PASS


def test_reserve_percent_above_hundred_fails(tmp_path):
    _p, _h, _t, _a, result = validate(
        tmp_path, reserve_calc=reserve(reserve_percent=101)
    )
    assert result.overall is ValidationStatus.FAIL
    assert "0..100" in (result.checks[0].reason or "")


@pytest.mark.parametrize(
    "field",
    ["reserve_percent", "reported_reserve_wh", "reported_usable_energy_wh"],
)
def test_reserve_fields_must_be_nonnegative(tmp_path, field):
    kwargs = {field: -1}
    _p, _h, _t, _a, result = validate(tmp_path, reserve_calc=reserve(**kwargs))
    assert result.overall is ValidationStatus.FAIL
    assert "must be non-negative" in (result.checks[0].reason or "")


@pytest.mark.parametrize("bad", [True, "20", None, [], {}])
def test_reserve_percent_must_be_numeric_not_bool(tmp_path, bad):
    _p, _h, _t, _a, result = validate(
        tmp_path, reserve_calc=reserve(reserve_percent=bad)
    )
    assert result.overall is ValidationStatus.FAIL
    assert "must be a number" in (result.checks[0].reason or "")


def test_reserve_keys_are_exact(tmp_path):
    _p, _h, _t, _a, result = validate(
        tmp_path, reserve_calc=reserve(extra={"endurance_min": 12})
    )
    assert result.overall is ValidationStatus.FAIL
    assert "must contain exactly" in (result.checks[0].reason or "")


def test_battery_keys_are_exact(tmp_path):
    _p, _h, _t, _a, result = validate(
        tmp_path, battery_calc=battery(extra={"cells": 6})
    )
    assert result.overall is ValidationStatus.FAIL
    assert "battery_energy calculation must contain exactly" in (result.checks[0].reason or "")


def test_missing_reserve_calculation_fails(tmp_path):
    _p, _h, _t, _a, result = validate(tmp_path, calculations=[battery()])
    assert result.overall is ValidationStatus.FAIL
    assert "must contain one reserve" in (result.checks[0].reason or "")


def test_multiple_reserve_calculations_fail(tmp_path):
    _p, _h, _t, _a, result = validate(
        tmp_path, calculations=[battery(), reserve(), reserve()]
    )
    assert result.overall is ValidationStatus.FAIL
    assert "exactly one reserve" in (result.checks[0].reason or "")


def test_missing_battery_energy_calculation_fails(tmp_path):
    _p, _h, _t, _a, result = validate(tmp_path, calculations=[reserve()])
    assert result.overall is ValidationStatus.FAIL
    assert "must contain one battery_energy" in (result.checks[0].reason or "")


def test_multiple_battery_energy_calculations_fail(tmp_path):
    _p, _h, _t, _a, result = validate(
        tmp_path, calculations=[battery(), battery(), reserve()]
    )
    assert result.overall is ValidationStatus.FAIL
    assert "exactly one battery_energy" in (result.checks[0].reason or "")


def test_unrelated_calculations_are_ignored(tmp_path):
    _p, _h, _t, _a, result = validate(
        tmp_path,
        calculations=[
            {"kind": "note", "value": 1},
            battery(),
            reserve(),
            {"kind": "other", "value": 2},
        ],
    )
    assert result.overall is ValidationStatus.PASS


def test_task_must_request_reserve_validator(tmp_path):
    projects, handle, _task, artifact = setup_valid(
        tmp_path,
        validators=("schema", "finite_values", "units", "battery_energy"),
    )
    with pytest.raises(ReserveValidationConflictError, match="did not request"):
        DeterministicReserveValidator(projects).validate(
            handle.project_id, artifact.record.artifact_id, 1
        )


def test_missing_artifact_is_conflict_not_engineering_fail(tmp_path):
    projects = ProjectStore(tmp_path / "projects")
    handle = projects.create_project()
    with pytest.raises(ReserveValidationConflictError, match="opened safely"):
        DeterministicReserveValidator(projects).validate(
            handle.project_id, "ART-000001", 1
        )


def test_tampered_blob_is_conflict_not_engineering_fail(tmp_path):
    projects, handle, _task, artifact = setup_valid(tmp_path)
    blob = ArtifactContentStore(projects).blob_path(handle.project_id, artifact.record.hash)
    blob.write_bytes(blob.read_bytes() + b"tamper")
    with pytest.raises(ReserveValidationConflictError, match="opened safely"):
        DeterministicReserveValidator(projects).validate(
            handle.project_id, artifact.record.artifact_id, 1
        )


def test_validation_does_not_change_draft_lifecycle(tmp_path):
    projects, handle, _task, artifact = setup_valid(tmp_path)
    DeterministicReserveValidator(projects).validate(
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
    DeterministicReserveValidator(projects).validate(
        handle.project_id, artifact.record.artifact_id, 1
    )
    assert tasks.open_task(handle.project_id, task.task_id) == before


def test_restart_is_deterministic(tmp_path):
    projects, handle, _task, artifact = setup_valid(tmp_path)
    first = DeterministicReserveValidator(projects).validate(
        handle.project_id, artifact.record.artifact_id, 1
    )
    restarted = ProjectStore(projects.projects_root)
    second = DeterministicReserveValidator(restarted).validate(
        handle.project_id, artifact.record.artifact_id, 1
    )
    assert second == first
