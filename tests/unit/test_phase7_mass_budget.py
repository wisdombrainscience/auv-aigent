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
    MASS_BUDGET_CALCULATION_KIND,
    MASS_BUDGET_VALIDATOR_CONTRACT_VERSION,
    MASS_BUDGET_VALIDATOR_ID,
    MAX_MASS_BUDGET_COMPONENTS,
    MAX_MASS_BUDGET_VALIDATION_BYTES,
    MAX_MASS_COMPONENT_NAME_CHARS,
    DeterministicMassBudgetValidator,
    MassBudgetValidationConflictError,
)


CREATED_AT = "2026-10-05T13:00:00Z"


def task_input(*, validators=("schema", "finite_values", "units", "mass_budget")):
    return TaskCreateInput(
        title="Validate deterministic mass budget",
        role="uav_systems_engineer",
        priority=100,
        dependencies=(),
        input_artifacts=(),
        output_schema=AGENT_RESULT_OUTPUT_SCHEMA_ID,
        validators=tuple(validators),
        review_required=False,
        max_attempts=3,
    )


def budget(
    *,
    components=None,
    reported_total_kg=3.5,
    limit_kg=4.0,
    extra=None,
):
    value = {
        "kind": "mass_budget",
        "components": components
        if components is not None
        else [
            {"name": "airframe", "mass_kg": 1.8},
            {"name": "battery", "mass_kg": 1.1},
            {"name": "payload", "mass_kg": 0.6},
        ],
        "reported_total_kg": reported_total_kg,
        "limit_kg": limit_kg,
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
                "statement": "Deterministic mass budget proposal.",
            }
        ],
        "assumptions": [],
        "calculations": calculations if calculations is not None else [budget()],
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


def validate(tmp_path: Path, mass_budget=None, *, calculations=None):
    if calculations is None:
        calculations = [mass_budget if mass_budget is not None else budget()]
    projects, handle, task, artifact = setup_valid(
        tmp_path, calculations=calculations
    )
    result = DeterministicMassBudgetValidator(projects).validate(
        handle.project_id, artifact.record.artifact_id, artifact.record.version
    )
    return projects, handle, task, artifact, result


def test_contract_constants_are_explicit():
    assert MASS_BUDGET_VALIDATOR_CONTRACT_VERSION == "1.0"
    assert MASS_BUDGET_VALIDATOR_ID == "mass_budget"
    assert MASS_BUDGET_CALCULATION_KIND == "mass_budget"
    assert MAX_MASS_BUDGET_COMPONENTS == 128
    assert MAX_MASS_COMPONENT_NAME_CHARS == 128
    assert MAX_MASS_BUDGET_VALIDATION_BYTES == 10 * 1024 * 1024


def test_valid_budget_passes_with_deterministic_evidence(tmp_path):
    _projects, _handle, _task, _artifact, result = validate(tmp_path)
    assert result.overall is ValidationStatus.PASS
    check = result.checks[0]
    assert check.check_id == "mass_budget"
    details = check.to_dict()["details"]
    assert details["component_count"] == 3
    assert details["computed_total_kg"] == 3.5
    assert details["reported_total_kg"] == 3.5
    assert details["limit_kg"] == 4
    assert details["reconciled"] is True
    assert details["within_limit"] is True
    assert details["unit"] == "kg"


def test_over_limit_is_deterministic_fail(tmp_path):
    _p, _h, _t, _a, result = validate(tmp_path, budget(limit_kg=3.4))
    assert result.overall is ValidationStatus.FAIL
    assert "exceeds limit_kg" in (result.checks[0].reason or "")
    assert result.checks[0].to_dict()["details"]["within_limit"] is False


def test_reported_total_mismatch_is_fail(tmp_path):
    _p, _h, _t, _a, result = validate(tmp_path, budget(reported_total_kg=3.6))
    assert result.overall is ValidationStatus.FAIL
    assert "does not equal deterministic component sum" in (result.checks[0].reason or "")


def test_decimal_bookkeeping_is_exact_for_json_decimal_values(tmp_path):
    b = budget(
        components=[
            {"name": "a", "mass_kg": 0.1},
            {"name": "b", "mass_kg": 0.2},
        ],
        reported_total_kg=0.3,
        limit_kg=1.0,
    )
    _p, _h, _t, _a, result = validate(tmp_path, b)
    assert result.overall is ValidationStatus.PASS
    assert result.checks[0].to_dict()["details"]["computed_total_kg"] == 0.3


def test_total_equal_to_limit_passes(tmp_path):
    _p, _h, _t, _a, result = validate(tmp_path, budget(limit_kg=3.5))
    assert result.overall is ValidationStatus.PASS


@pytest.mark.parametrize(
    "components,reason",
    [
        ([], "must not be empty"),
        ([{"name": "a", "mass_kg": -0.1}], "must be non-negative"),
        ([{"name": "a", "mass_kg": True}], "must be a number"),
        ([{"name": "a", "mass_kg": "1"}], "must be a number"),
        ([{"name": "", "mass_kg": 1}], "clean non-empty"),
        (
            [
                {"name": "same", "mass_kg": 1},
                {"name": "same", "mass_kg": 2},
            ],
            "duplicate mass component name",
        ),
        ([{"name": "a", "mass_kg": 1, "unit": "kg"}], "exactly name,mass_kg"),
    ],
)
def test_invalid_component_contract_fails(tmp_path, components, reason):
    _p, _h, _t, _a, result = validate(
        tmp_path,
        budget(components=components, reported_total_kg=0 if not components else 1),
    )
    assert result.overall is ValidationStatus.FAIL
    assert reason in (result.checks[0].reason or "")


def test_component_count_is_bounded(tmp_path):
    components = [
        {"name": f"c{i}", "mass_kg": 0}
        for i in range(MAX_MASS_BUDGET_COMPONENTS + 1)
    ]
    _p, _h, _t, _a, result = validate(
        tmp_path, budget(components=components, reported_total_kg=0)
    )
    assert result.overall is ValidationStatus.FAIL
    assert "MAX_MASS_BUDGET_COMPONENTS" in (result.checks[0].reason or "")


def test_component_name_length_is_bounded(tmp_path):
    _p, _h, _t, _a, result = validate(
        tmp_path,
        budget(
            components=[{"name": "x" * 129, "mass_kg": 1}],
            reported_total_kg=1,
        ),
    )
    assert result.overall is ValidationStatus.FAIL
    assert "MAX_MASS_COMPONENT_NAME_CHARS" in (result.checks[0].reason or "")


@pytest.mark.parametrize("field", ["reported_total_kg", "limit_kg"])
def test_totals_must_be_nonnegative_numbers(tmp_path, field):
    kwargs = {field: -1}
    _p, _h, _t, _a, result = validate(tmp_path, budget(**kwargs))
    assert result.overall is ValidationStatus.FAIL
    assert "must be non-negative" in (result.checks[0].reason or "")


def test_budget_keys_are_exact(tmp_path):
    _p, _h, _t, _a, result = validate(tmp_path, budget(extra={"notes": "x"}))
    assert result.overall is ValidationStatus.FAIL
    assert "must contain exactly" in (result.checks[0].reason or "")


def test_missing_mass_budget_calculation_fails(tmp_path):
    _p, _h, _t, _a, result = validate(
        tmp_path, calculations=[{"kind": "energy_budget", "energy_wh": 10}]
    )
    assert result.overall is ValidationStatus.FAIL
    assert "must contain one mass_budget" in (result.checks[0].reason or "")


def test_multiple_mass_budget_calculations_fail(tmp_path):
    _p, _h, _t, _a, result = validate(
        tmp_path, calculations=[budget(), budget()]
    )
    assert result.overall is ValidationStatus.FAIL
    assert "exactly one mass_budget" in (result.checks[0].reason or "")


def test_unrelated_calculations_are_ignored(tmp_path):
    _p, _h, _t, _a, result = validate(
        tmp_path,
        calculations=[
            {"kind": "note", "value": 1},
            budget(),
            {"kind": "other", "value": 2},
        ],
    )
    assert result.overall is ValidationStatus.PASS


def test_task_must_request_mass_budget_validator(tmp_path):
    projects, handle, _task, artifact = setup_valid(
        tmp_path,
        validators=("schema", "finite_values", "units"),
    )
    with pytest.raises(MassBudgetValidationConflictError, match="did not request"):
        DeterministicMassBudgetValidator(projects).validate(
            handle.project_id, artifact.record.artifact_id, 1
        )


def test_missing_artifact_is_conflict_not_engineering_fail(tmp_path):
    projects = ProjectStore(tmp_path / "projects")
    handle = projects.create_project()
    with pytest.raises(MassBudgetValidationConflictError, match="opened safely"):
        DeterministicMassBudgetValidator(projects).validate(
            handle.project_id, "ART-000001", 1
        )


def test_tampered_blob_is_conflict_not_engineering_fail(tmp_path):
    projects, handle, _task, artifact = setup_valid(tmp_path)
    blob = ArtifactContentStore(projects).blob_path(
        handle.project_id, artifact.record.hash
    )
    blob.write_bytes(blob.read_bytes() + b"tamper")
    with pytest.raises(MassBudgetValidationConflictError, match="opened safely"):
        DeterministicMassBudgetValidator(projects).validate(
            handle.project_id, artifact.record.artifact_id, 1
        )


def test_validation_does_not_change_draft_lifecycle(tmp_path):
    projects, handle, _task, artifact = setup_valid(tmp_path)
    DeterministicMassBudgetValidator(projects).validate(
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
    DeterministicMassBudgetValidator(projects).validate(
        handle.project_id, artifact.record.artifact_id, 1
    )
    assert tasks.open_task(handle.project_id, task.task_id) == before


def test_restart_is_deterministic(tmp_path):
    projects, handle, _task, artifact = setup_valid(tmp_path)
    first = DeterministicMassBudgetValidator(projects).validate(
        handle.project_id, artifact.record.artifact_id, 1
    )
    restarted = ProjectStore(projects.projects_root)
    second = DeterministicMassBudgetValidator(restarted).validate(
        handle.project_id, artifact.record.artifact_id, 1
    )
    assert second == first
