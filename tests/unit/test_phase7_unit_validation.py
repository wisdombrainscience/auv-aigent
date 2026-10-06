from __future__ import annotations

import json
from pathlib import Path

import pytest

from ai.agents import (
    AGENT_RESULT_OUTPUT_SCHEMA_ID,
    AgentRunStore,
    ContextManifestStore,
    MinimalContextBuilder,
    RESULT_DRAFT_ARTIFACT_TYPE,
    ResultDraftArtifactStore,
    SchemaEnforcedOutputValidator,
)
from ai.artifacts import ArtifactVersionInput, ArtifactVersionStore
from ai.artifacts.hashing import ArtifactContentStore
from ai.contracts.artifact_states import ArtifactState
from ai.contracts.validation_result import ValidationStatus
from ai.project.store import ProjectStore
from ai.providers.contracts import GenerateResponse
from ai.tasks import TaskCreateInput, TaskStore
from ai.validators import (
    CANONICAL_ENGINEERING_UNITS,
    MAX_UNIT_SCAN_DEPTH,
    MAX_UNIT_SCAN_NODES,
    MAX_UNIT_TEXT_CHARS,
    MAX_UNIT_VALIDATION_BYTES,
    UNIT_VALIDATOR_CONTRACT_VERSION,
    UNIT_VALIDATOR_ID,
    DeterministicUnitValidator,
    UnitValidationConflictError,
)


CREATED_AT = "2026-10-05T12:30:00Z"


def task_input(*, validators=("schema", "finite_values", "units")):
    return TaskCreateInput(
        title="Validate engineering units",
        role="uav_systems_engineer",
        priority=100,
        dependencies=(),
        input_artifacts=(),
        output_schema=AGENT_RESULT_OUTPUT_SCHEMA_ID,
        validators=tuple(validators),
        review_required=False,
        max_attempts=3,
    )


def response(*, calculations=None):
    payload = {
        "status": "PROPOSED",
        "claims": [
            {
                "classification": "AI_PROPOSAL",
                "statement": "Bounded deterministic unit validation fixture.",
            }
        ],
        "assumptions": [],
        "calculations": calculations
        if calculations is not None
        else [
            {
                "mass": {"value": 2.1, "unit": "kg"},
                "battery_energy": {"value": 60.0, "unit": "Wh"},
                "reserve_percent": 20.0,
            }
        ],
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


def setup_valid(tmp_path: Path, *, calculations=None):
    projects = ProjectStore(tmp_path / "remote-uav" / "projects")
    handle = projects.create_project()
    tasks = TaskStore(projects)
    task = tasks.create_task(handle.project_id, task_input())
    context = MinimalContextBuilder(projects).build(handle.project_id, task.task_id)
    output = SchemaEnforcedOutputValidator(projects).enforce(
        handle.project_id, task.task_id, response(calculations=calculations)
    )
    run = AgentRunStore(projects).create_run(output)
    ContextManifestStore(projects).create_manifest(run, context)
    artifact = ResultDraftArtifactStore(projects).create_artifact(
        handle.project_id, run.run_id, created_timestamp=CREATED_AT
    )
    return projects, handle, task, artifact


def create_raw_artifact(
    projects: ProjectStore,
    handle,
    task,
    *,
    content: bytes,
    artifact_type: str = RESULT_DRAFT_ARTIFACT_TYPE,
):
    return ArtifactVersionStore(projects).create_artifact(
        handle.project_id,
        ArtifactVersionInput(
            type=artifact_type,
            producer_task=task.task_id,
            input_dependency_versions=(),
            content=content,
            created_timestamp=CREATED_AT,
        ),
    )


def make_raw(
    tmp_path: Path,
    content: bytes,
    *,
    validators=("units",),
    artifact_type=RESULT_DRAFT_ARTIFACT_TYPE,
):
    projects = ProjectStore(tmp_path / "projects")
    handle = projects.create_project()
    task = TaskStore(projects).create_task(
        handle.project_id, task_input(validators=validators)
    )
    artifact = create_raw_artifact(
        projects,
        handle,
        task,
        content=content,
        artifact_type=artifact_type,
    )
    return projects, handle, task, artifact


def validate_raw(tmp_path: Path, payload: object):
    raw = (json.dumps(payload, separators=(",", ":"), allow_nan=False) + "\n").encode()
    projects, handle, _task, artifact = make_raw(tmp_path, raw)
    result = DeterministicUnitValidator(projects).validate(
        handle.project_id, artifact.artifact_id, 1
    )
    return result


def test_contract_constants_are_explicit():
    assert UNIT_VALIDATOR_CONTRACT_VERSION == "1.0"
    assert UNIT_VALIDATOR_ID == "units"
    assert MAX_UNIT_VALIDATION_BYTES == 10 * 1024 * 1024
    assert MAX_UNIT_SCAN_NODES == 100_000
    assert MAX_UNIT_SCAN_DEPTH == 128
    assert MAX_UNIT_TEXT_CHARS == 16


def test_canonical_registry_is_fixed_for_current_engineering_scope():
    assert dict(CANONICAL_ENGINEERING_UNITS) == {
        "1": "dimensionless",
        "%": "ratio",
        "A": "current",
        "Ah": "charge_capacity",
        "N": "force",
        "V": "voltage",
        "W": "power",
        "Wh": "energy",
        "deg": "angle",
        "g": "mass",
        "h": "time",
        "kg": "mass",
        "km": "length",
        "m": "length",
        "m/s": "speed",
        "m/s^2": "acceleration",
        "min": "time",
        "rad": "angle",
        "s": "time",
    }


def test_valid_g68_artifact_returns_pass(tmp_path):
    projects, handle, _task, artifact = setup_valid(tmp_path)
    result = DeterministicUnitValidator(projects).validate(
        handle.project_id, artifact.record.artifact_id, artifact.record.version
    )
    assert result.overall is ValidationStatus.PASS
    assert result.checks[0].check_id == "units"


@pytest.mark.parametrize("unit", sorted(CANONICAL_ENGINEERING_UNITS))
def test_each_canonical_explicit_unit_is_accepted(tmp_path, unit):
    result = validate_raw(tmp_path, {"quantity": {"value": 1, "unit": unit}})
    assert result.overall is ValidationStatus.PASS


@pytest.mark.parametrize(
    "unit",
    ["KG", "lb", "lbs", "ft", "mph", "mAh", "kph", "percent", " wh", "Wh "],
)
def test_noncanonical_or_unsupported_explicit_unit_fails(tmp_path, unit):
    result = validate_raw(tmp_path, {"quantity": {"value": 1, "unit": unit}})
    assert result.overall is ValidationStatus.FAIL
    assert "unsupported or non-canonical" in (result.checks[0].reason or "") or "clean non-empty" in (result.checks[0].reason or "")


def test_unit_annotation_does_not_require_value_member(tmp_path):
    result = validate_raw(
        tmp_path,
        {"check": {"observed": 2.5, "limit": 3.0, "unit": "kg"}},
    )
    assert result.overall is ValidationStatus.PASS
    assert result.checks[0].to_dict()["details"]["unit_annotations_checked"] == 1


def test_explicit_value_with_unit_must_be_numeric(tmp_path):
    result = validate_raw(tmp_path, {"mass": {"value": "2.5", "unit": "kg"}})
    assert result.overall is ValidationStatus.FAIL
    assert "must be a finite number" in (result.checks[0].reason or "")


def test_boolean_is_not_a_quantity_value(tmp_path):
    result = validate_raw(tmp_path, {"mass": {"value": True, "unit": "kg"}})
    assert result.overall is ValidationStatus.FAIL


def test_field_suffix_and_explicit_unit_must_agree(tmp_path):
    result = validate_raw(tmp_path, {"mass_kg": {"value": 2100, "unit": "g"}})
    assert result.overall is ValidationStatus.FAIL
    assert "conflicts with field suffix unit 'kg'" in (result.checks[0].reason or "")


def test_matching_field_suffix_and_explicit_unit_passes(tmp_path):
    result = validate_raw(tmp_path, {"mass_kg": {"value": 2.1, "unit": "kg"}})
    assert result.overall is ValidationStatus.PASS


def test_existing_canonical_suffix_fields_are_counted(tmp_path):
    result = validate_raw(
        tmp_path,
        {
            "mass_kg": 2.1,
            "capacity_wh": 60,
            "reserve_percent": 20,
            "gravity_m_s2": 9.81,
            "speed_m_s": 5,
            "range_km": 2,
            "yaw_deg": 45,
        },
    )
    assert result.overall is ValidationStatus.PASS
    assert result.checks[0].to_dict()["details"]["unit_suffix_values_checked"] == 7


def test_unannotated_number_is_not_rejected_by_units_gate(tmp_path):
    result = validate_raw(tmp_path, {"rotors": 4, "ratio": 1.2})
    assert result.overall is ValidationStatus.PASS
    details = result.checks[0].to_dict()["details"]
    assert details["unit_annotations_checked"] == 0
    assert details["unit_suffix_values_checked"] == 0


def test_generic_artifact_type_is_supported(tmp_path):
    projects, handle, _task, artifact = make_raw(
        tmp_path,
        b'{"mass":{"value":2.5,"unit":"kg"}}\n',
        artifact_type="engineering.future.v1",
    )
    result = DeterministicUnitValidator(projects).validate(
        handle.project_id, artifact.artifact_id, 1
    )
    assert result.overall is ValidationStatus.PASS
    assert result.checks[0].to_dict()["details"]["artifact_type"] == "engineering.future.v1"


def test_invalid_json_returns_fail_not_exception(tmp_path):
    projects, handle, _task, artifact = make_raw(tmp_path, b"{not-json}\n")
    result = DeterministicUnitValidator(projects).validate(
        handle.project_id, artifact.artifact_id, 1
    )
    assert result.overall is ValidationStatus.FAIL
    assert "invalid JSON" in (result.checks[0].reason or "")


def test_non_utf8_returns_fail(tmp_path):
    projects, handle, _task, artifact = make_raw(tmp_path, b"\xff\xfe")
    result = DeterministicUnitValidator(projects).validate(
        handle.project_id, artifact.artifact_id, 1
    )
    assert result.overall is ValidationStatus.FAIL
    assert "not UTF-8" in (result.checks[0].reason or "")


def test_duplicate_json_key_returns_fail(tmp_path):
    projects, handle, _task, artifact = make_raw(
        tmp_path, b'{"mass":{"unit":"kg","unit":"g"}}\n'
    )
    result = DeterministicUnitValidator(projects).validate(
        handle.project_id, artifact.artifact_id, 1
    )
    assert result.overall is ValidationStatus.FAIL
    assert "duplicate JSON key" in (result.checks[0].reason or "")


def test_nonfinite_explicit_quantity_is_rejected_for_safe_unit_interpretation(tmp_path):
    projects, handle, _task, artifact = make_raw(
        tmp_path, b'{"mass":{"value":NaN,"unit":"kg"}}\n'
    )
    result = DeterministicUnitValidator(projects).validate(
        handle.project_id, artifact.artifact_id, 1
    )
    assert result.overall is ValidationStatus.FAIL
    assert "non-finite" in (result.checks[0].reason or "")


def test_task_must_request_units_validator(tmp_path):
    projects, handle, _task, artifact = make_raw(
        tmp_path, b'{"mass_kg":1}\n', validators=("schema", "finite_values")
    )
    with pytest.raises(UnitValidationConflictError, match="did not request"):
        DeterministicUnitValidator(projects).validate(
            handle.project_id, artifact.artifact_id, 1
        )


def test_missing_artifact_is_conflict_not_engineering_fail(tmp_path):
    projects = ProjectStore(tmp_path / "projects")
    handle = projects.create_project()
    with pytest.raises(UnitValidationConflictError, match="opened safely"):
        DeterministicUnitValidator(projects).validate(
            handle.project_id, "ART-000001", 1
        )


def test_tampered_blob_is_conflict_not_engineering_fail(tmp_path):
    projects, handle, _task, artifact = make_raw(tmp_path, b'{"mass_kg":1}\n')
    blob = ArtifactContentStore(projects).blob_path(handle.project_id, artifact.hash)
    blob.write_bytes(b'{"mass_kg":2}\n')
    with pytest.raises(UnitValidationConflictError, match="opened safely"):
        DeterministicUnitValidator(projects).validate(
            handle.project_id, artifact.artifact_id, 1
        )


def test_validation_does_not_change_draft_lifecycle(tmp_path):
    projects, handle, _task, artifact = setup_valid(tmp_path)
    DeterministicUnitValidator(projects).validate(
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
    DeterministicUnitValidator(projects).validate(
        handle.project_id, artifact.record.artifact_id, 1
    )
    assert tasks.open_task(handle.project_id, task.task_id) == before


def test_restart_is_deterministic(tmp_path):
    projects, handle, _task, artifact = setup_valid(tmp_path)
    first = DeterministicUnitValidator(projects).validate(
        handle.project_id, artifact.record.artifact_id, 1
    )
    restarted = ProjectStore(projects.projects_root)
    second = DeterministicUnitValidator(restarted).validate(
        handle.project_id, artifact.record.artifact_id, 1
    )
    assert second == first


def test_pass_details_are_deterministic(tmp_path):
    projects, handle, _task, artifact = make_raw(
        tmp_path,
        b'{"mass":{"value":2.5,"unit":"kg"},"reserve_percent":20}\n',
    )
    result = DeterministicUnitValidator(projects).validate(
        handle.project_id, artifact.artifact_id, 1
    )
    assert result.checks[0].to_dict()["details"] == {
        "validator_contract_version": "1.0",
        "artifact_type": RESULT_DRAFT_ARTIFACT_TYPE,
        "canonical_unit_registry_size": 19,
        "unit_annotations_checked": 1,
        "unit_suffix_values_checked": 1,
    }


def test_validator_has_no_lifecycle_or_normalization_api(tmp_path):
    projects = ProjectStore(tmp_path / "projects")
    validator = DeterministicUnitValidator(projects)
    for forbidden in (
        "transition",
        "approve",
        "review",
        "persist_result",
        "convert",
        "normalize",
    ):
        assert not hasattr(validator, forbidden)


def test_validator_has_no_provider_or_uav_runtime_dependency():
    import ai.validators.units as module

    source = Path(module.__file__).read_text(encoding="utf-8")
    assert "ai.providers" not in source
    assert "mavsdk" not in source.lower()
    assert "px4" not in source.lower()
    assert "gazebo" not in source.lower()
