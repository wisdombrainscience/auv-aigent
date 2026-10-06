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
    FINITE_NUMBER_VALIDATOR_CONTRACT_VERSION,
    FINITE_NUMBER_VALIDATOR_ID,
    MAX_FINITE_NUMBER_VALIDATION_BYTES,
    DeterministicFiniteNumberValidator,
    FiniteNumberValidationConflictError,
)


CREATED_AT = "2026-10-05T10:30:00Z"


def task_input(*, validators=("schema", "finite_values")):
    return TaskCreateInput(
        title="Validate finite engineering numbers",
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
                "statement": "Bounded finite-number validation fixture.",
            }
        ],
        "assumptions": [],
        "calculations": calculations if calculations is not None else [{"mass_kg": 2.1}],
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


def make_raw(tmp_path: Path, content: bytes, *, validators=("finite_values",), artifact_type=RESULT_DRAFT_ARTIFACT_TYPE):
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


def test_contract_constants_are_explicit():
    assert FINITE_NUMBER_VALIDATOR_CONTRACT_VERSION == "1.0"
    assert FINITE_NUMBER_VALIDATOR_ID == "finite_values"
    assert MAX_FINITE_NUMBER_VALIDATION_BYTES == 10 * 1024 * 1024


def test_valid_g68_artifact_returns_pass(tmp_path):
    projects, handle, _task, artifact = setup_valid(tmp_path)
    result = DeterministicFiniteNumberValidator(projects).validate(
        handle.project_id, artifact.record.artifact_id, artifact.record.version
    )
    assert result.overall is ValidationStatus.PASS
    assert result.checks[0].check_id == "finite_values"
    assert result.checks[0].status is ValidationStatus.PASS


def test_nested_finite_numbers_are_counted_deterministically(tmp_path):
    projects, handle, _task, artifact = setup_valid(
        tmp_path,
        calculations=[
            {"mass_kg": 2.1, "count": 4, "nested": [0, -3.5, {"ratio": 1.25}]}
        ],
    )
    result = DeterministicFiniteNumberValidator(projects).validate(
        handle.project_id, artifact.record.artifact_id, 1
    )
    details = result.checks[0].to_dict()["details"]
    assert details["numeric_values_checked"] == 5


def test_boolean_is_not_counted_as_numeric_value(tmp_path):
    projects, handle, _task, artifact = make_raw(
        tmp_path, b'{"enabled":true,"count":1}\n'
    )
    result = DeterministicFiniteNumberValidator(projects).validate(
        handle.project_id, artifact.artifact_id, 1
    )
    assert result.overall is ValidationStatus.PASS
    assert result.checks[0].to_dict()["details"]["numeric_values_checked"] == 1


@pytest.mark.parametrize("constant", ["NaN", "Infinity", "-Infinity"])
def test_nonfinite_json_constants_return_fail(tmp_path, constant):
    projects, handle, _task, artifact = make_raw(
        tmp_path, ('{"value":' + constant + '}\n').encode()
    )
    result = DeterministicFiniteNumberValidator(projects).validate(
        handle.project_id, artifact.artifact_id, 1
    )
    assert result.overall is ValidationStatus.FAIL
    assert "non-finite" in (result.checks[0].reason or "")


@pytest.mark.parametrize("token", ["1e309", "-1e309"])
def test_float_overflow_returns_fail(tmp_path, token):
    projects, handle, _task, artifact = make_raw(
        tmp_path, ('{"value":' + token + '}\n').encode()
    )
    result = DeterministicFiniteNumberValidator(projects).validate(
        handle.project_id, artifact.artifact_id, 1
    )
    assert result.overall is ValidationStatus.FAIL
    assert "$.value" in (result.checks[0].reason or "")
    assert "non-finite" in (result.checks[0].reason or "")


def test_huge_integer_outside_finite_float_range_returns_fail(tmp_path):
    raw = ('{"value":' + ('9' * 400) + '}\n').encode()
    projects, handle, _task, artifact = make_raw(tmp_path, raw)
    result = DeterministicFiniteNumberValidator(projects).validate(
        handle.project_id, artifact.artifact_id, 1
    )
    assert result.overall is ValidationStatus.FAIL
    assert "finite floating-point range" in (result.checks[0].reason or "")


def test_nested_failure_reports_stable_path(tmp_path):
    projects, handle, _task, artifact = make_raw(
        tmp_path, b'{"calculations":[{"mass":{"kg":1e309}}]}\n'
    )
    result = DeterministicFiniteNumberValidator(projects).validate(
        handle.project_id, artifact.artifact_id, 1
    )
    assert result.overall is ValidationStatus.FAIL
    assert "$.calculations[0].mass.kg" in (result.checks[0].reason or "")


def test_invalid_json_returns_fail_not_exception(tmp_path):
    projects, handle, _task, artifact = make_raw(tmp_path, b"{not-json}\n")
    result = DeterministicFiniteNumberValidator(projects).validate(
        handle.project_id, artifact.artifact_id, 1
    )
    assert result.overall is ValidationStatus.FAIL
    assert "invalid JSON" in (result.checks[0].reason or "")


def test_non_utf8_returns_fail(tmp_path):
    projects, handle, _task, artifact = make_raw(tmp_path, b"\xff\xfe")
    result = DeterministicFiniteNumberValidator(projects).validate(
        handle.project_id, artifact.artifact_id, 1
    )
    assert result.overall is ValidationStatus.FAIL
    assert "not UTF-8" in (result.checks[0].reason or "")


def test_duplicate_json_key_returns_fail(tmp_path):
    projects, handle, _task, artifact = make_raw(
        tmp_path, b'{"value":1,"value":2}\n'
    )
    result = DeterministicFiniteNumberValidator(projects).validate(
        handle.project_id, artifact.artifact_id, 1
    )
    assert result.overall is ValidationStatus.FAIL
    assert "duplicate JSON key" in (result.checks[0].reason or "")


def test_top_level_array_is_allowed_for_finite_check(tmp_path):
    projects, handle, _task, artifact = make_raw(tmp_path, b"[1,2.5,-3]\n")
    result = DeterministicFiniteNumberValidator(projects).validate(
        handle.project_id, artifact.artifact_id, 1
    )
    assert result.overall is ValidationStatus.PASS
    assert result.checks[0].to_dict()["details"]["numeric_values_checked"] == 3


def test_unknown_artifact_type_is_allowed_for_generic_finite_check(tmp_path):
    projects, handle, _task, artifact = make_raw(
        tmp_path,
        b'{"value":2.5}\n',
        artifact_type="engineering.future.v1",
    )
    result = DeterministicFiniteNumberValidator(projects).validate(
        handle.project_id, artifact.artifact_id, 1
    )
    assert result.overall is ValidationStatus.PASS
    assert result.checks[0].to_dict()["details"]["artifact_type"] == "engineering.future.v1"


def test_task_must_request_finite_values_validator(tmp_path):
    projects, handle, _task, artifact = make_raw(
        tmp_path, b'{"value":1}\n', validators=("schema",)
    )
    with pytest.raises(FiniteNumberValidationConflictError, match="did not request"):
        DeterministicFiniteNumberValidator(projects).validate(
            handle.project_id, artifact.artifact_id, 1
        )


def test_missing_artifact_is_conflict_not_engineering_fail(tmp_path):
    projects = ProjectStore(tmp_path / "projects")
    handle = projects.create_project()
    with pytest.raises(FiniteNumberValidationConflictError, match="opened safely"):
        DeterministicFiniteNumberValidator(projects).validate(
            handle.project_id, "ART-000001", 1
        )


def test_tampered_blob_is_conflict_not_engineering_fail(tmp_path):
    projects, handle, _task, artifact = make_raw(tmp_path, b'{"value":1}\n')
    content = ArtifactContentStore(projects)
    blob = content.blob_path(handle.project_id, artifact.hash)
    blob.write_bytes(b'{"value":2}\n')
    with pytest.raises(FiniteNumberValidationConflictError, match="opened safely"):
        DeterministicFiniteNumberValidator(projects).validate(
            handle.project_id, artifact.artifact_id, 1
        )


def test_validation_does_not_change_draft_lifecycle(tmp_path):
    projects, handle, _task, artifact = setup_valid(tmp_path)
    DeterministicFiniteNumberValidator(projects).validate(
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
    DeterministicFiniteNumberValidator(projects).validate(
        handle.project_id, artifact.record.artifact_id, 1
    )
    after = tasks.open_task(handle.project_id, task.task_id)
    assert after == before


def test_restart_is_deterministic(tmp_path):
    projects, handle, _task, artifact = setup_valid(tmp_path)
    first = DeterministicFiniteNumberValidator(projects).validate(
        handle.project_id, artifact.record.artifact_id, 1
    )
    restarted = ProjectStore(projects.projects_root)
    second = DeterministicFiniteNumberValidator(restarted).validate(
        handle.project_id, artifact.record.artifact_id, 1
    )
    assert second == first


def test_pass_details_are_deterministic(tmp_path):
    projects, handle, _task, artifact = make_raw(tmp_path, b'{"a":1,"b":2.5}\n')
    result = DeterministicFiniteNumberValidator(projects).validate(
        handle.project_id, artifact.artifact_id, 1
    )
    assert result.checks[0].to_dict()["details"] == {
        "validator_contract_version": "1.0",
        "artifact_type": RESULT_DRAFT_ARTIFACT_TYPE,
        "numeric_values_checked": 2,
    }


def test_validator_has_no_lifecycle_transition_api(tmp_path):
    projects = ProjectStore(tmp_path / "projects")
    validator = DeterministicFiniteNumberValidator(projects)
    for forbidden in ("transition", "approve", "review", "persist_result"):
        assert not hasattr(validator, forbidden)


def test_validator_has_no_provider_or_uav_runtime_dependency():
    import ai.validators.finite as module

    source = Path(module.__file__).read_text(encoding="utf-8")
    assert "ai.providers" not in source
    assert "mavsdk" not in source.lower()
    assert "px4" not in source.lower()
    assert "gazebo" not in source.lower()
