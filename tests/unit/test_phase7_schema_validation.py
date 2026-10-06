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
from ai.contracts.artifact_states import ArtifactState
from ai.contracts.validation_result import ValidationStatus
from ai.project.store import ProjectStore
from ai.providers.contracts import GenerateResponse
from ai.tasks import TaskCreateInput, TaskStore
from ai.validators import (
    DEFAULT_ARTIFACT_SCHEMA_REGISTRY,
    MAX_SCHEMA_VALIDATION_BYTES,
    SCHEMA_VALIDATOR_CONTRACT_VERSION,
    SCHEMA_VALIDATOR_ID,
    ArtifactSchemaDefinition,
    ArtifactSchemaRegistry,
    DeterministicSchemaValidator,
    SchemaRegistryError,
    SchemaValidationConflictError,
    UnknownArtifactSchemaError,
)


CREATED_AT = "2026-10-05T09:30:00Z"


def task_input(*, validators=("schema",)):
    return TaskCreateInput(
        title="Validate one deterministic artifact schema",
        role="uav_systems_engineer",
        priority=100,
        dependencies=(),
        input_artifacts=(),
        output_schema=AGENT_RESULT_OUTPUT_SCHEMA_ID,
        validators=tuple(validators),
        review_required=False,
        max_attempts=3,
    )


def response():
    payload = {
        "status": "PROPOSED",
        "claims": [
            {
                "classification": "AI_PROPOSAL",
                "statement": "Bounded schema-validation fixture.",
            }
        ],
        "assumptions": [],
        "calculations": [{"mass_kg": 2.1}],
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


def setup_valid(tmp_path: Path):
    projects = ProjectStore(tmp_path / "remote-uav" / "projects")
    handle = projects.create_project()
    tasks = TaskStore(projects)
    task = tasks.create_task(handle.project_id, task_input())
    context = MinimalContextBuilder(projects).build(handle.project_id, task.task_id)
    output = SchemaEnforcedOutputValidator(projects).enforce(
        handle.project_id, task.task_id, response()
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


def test_contract_constants_are_explicit():
    assert SCHEMA_VALIDATOR_CONTRACT_VERSION == "1.0"
    assert SCHEMA_VALIDATOR_ID == "schema"
    assert MAX_SCHEMA_VALIDATION_BYTES == 10 * 1024 * 1024


def test_default_registry_enables_only_current_result_artifact_type():
    assert DEFAULT_ARTIFACT_SCHEMA_REGISTRY.artifact_types() == (
        RESULT_DRAFT_ARTIFACT_TYPE,
    )


def test_registry_definition_is_immutable():
    definition = DEFAULT_ARTIFACT_SCHEMA_REGISTRY.get(RESULT_DRAFT_ARTIFACT_TYPE)
    with pytest.raises(Exception):
        definition.artifact_type = "other.v1"  # type: ignore[misc]


def test_registry_rejects_empty_definitions():
    with pytest.raises(SchemaRegistryError, match="at least one"):
        ArtifactSchemaRegistry(())


def test_registry_rejects_duplicate_artifact_type():
    parser = lambda raw: raw
    definition = ArtifactSchemaDefinition("demo.v1", "Demo", parser)
    with pytest.raises(SchemaRegistryError, match="duplicate"):
        ArtifactSchemaRegistry((definition, definition))


def test_registry_unknown_type_fails_closed():
    with pytest.raises(UnknownArtifactSchemaError, match="no V7.1 schema"):
        DEFAULT_ARTIFACT_SCHEMA_REGISTRY.get("unknown.v1")


def test_valid_g68_artifact_returns_pass_validation_result(tmp_path):
    projects, handle, _task, artifact = setup_valid(tmp_path)
    result = DeterministicSchemaValidator(projects).validate(
        handle.project_id, artifact.record.artifact_id, artifact.record.version
    )
    assert result.overall is ValidationStatus.PASS
    assert len(result.checks) == 1
    assert result.checks[0].check_id == "schema"
    assert result.checks[0].status is ValidationStatus.PASS
    assert result.checks[0].reason is None


def test_validation_does_not_change_draft_lifecycle(tmp_path):
    projects, handle, _task, artifact = setup_valid(tmp_path)
    DeterministicSchemaValidator(projects).validate(
        handle.project_id, artifact.record.artifact_id, 1
    )
    reopened = ArtifactVersionStore(projects).open_version(
        handle.project_id, artifact.record.artifact_id, 1
    )
    assert reopened.status is ArtifactState.DRAFT
    assert reopened.validation_result is None


def test_validation_does_not_change_task_state(tmp_path):
    projects, handle, task, artifact = setup_valid(tmp_path)
    before = TaskStore(projects).open_task(handle.project_id, task.task_id)
    DeterministicSchemaValidator(projects).validate(
        handle.project_id, artifact.record.artifact_id, 1
    )
    after = TaskStore(projects).open_task(handle.project_id, task.task_id)
    assert after == before


def test_restart_is_deterministic(tmp_path):
    projects, handle, _task, artifact = setup_valid(tmp_path)
    first = DeterministicSchemaValidator(projects).validate(
        handle.project_id, artifact.record.artifact_id, 1
    )
    restarted = ProjectStore(projects.projects_root)
    second = DeterministicSchemaValidator(restarted).validate(
        handle.project_id, artifact.record.artifact_id, 1
    )
    assert second == first


def test_invalid_json_returns_fail_not_exception(tmp_path):
    projects = ProjectStore(tmp_path / "projects")
    handle = projects.create_project()
    task = TaskStore(projects).create_task(handle.project_id, task_input())
    artifact = create_raw_artifact(projects, handle, task, content=b"{not-json}\n")
    result = DeterministicSchemaValidator(projects).validate(
        handle.project_id, artifact.artifact_id, 1
    )
    assert result.overall is ValidationStatus.FAIL
    assert "invalid JSON" in (result.checks[0].reason or "")


def test_top_level_array_returns_fail(tmp_path):
    projects = ProjectStore(tmp_path / "projects")
    handle = projects.create_project()
    task = TaskStore(projects).create_task(handle.project_id, task_input())
    artifact = create_raw_artifact(projects, handle, task, content=b"[]\n")
    result = DeterministicSchemaValidator(projects).validate(
        handle.project_id, artifact.artifact_id, 1
    )
    assert result.overall is ValidationStatus.FAIL
    assert "top level" in (result.checks[0].reason or "")


def test_duplicate_json_key_returns_fail(tmp_path):
    projects = ProjectStore(tmp_path / "projects")
    handle = projects.create_project()
    task = TaskStore(projects).create_task(handle.project_id, task_input())
    raw = b'{"contract_version":"1.0","contract_version":"1.0"}\n'
    artifact = create_raw_artifact(projects, handle, task, content=raw)
    result = DeterministicSchemaValidator(projects).validate(
        handle.project_id, artifact.artifact_id, 1
    )
    assert result.overall is ValidationStatus.FAIL
    assert "duplicate JSON key" in (result.checks[0].reason or "")


def test_nonfinite_json_returns_fail(tmp_path):
    projects = ProjectStore(tmp_path / "projects")
    handle = projects.create_project()
    task = TaskStore(projects).create_task(handle.project_id, task_input())
    artifact = create_raw_artifact(
        projects, handle, task, content=b'{"value":NaN}\n'
    )
    result = DeterministicSchemaValidator(projects).validate(
        handle.project_id, artifact.artifact_id, 1
    )
    assert result.overall is ValidationStatus.FAIL
    assert "non-finite" in (result.checks[0].reason or "")


def test_non_utf8_returns_fail(tmp_path):
    projects = ProjectStore(tmp_path / "projects")
    handle = projects.create_project()
    task = TaskStore(projects).create_task(handle.project_id, task_input())
    artifact = create_raw_artifact(projects, handle, task, content=b"\xff\xfe")
    result = DeterministicSchemaValidator(projects).validate(
        handle.project_id, artifact.artifact_id, 1
    )
    assert result.overall is ValidationStatus.FAIL
    assert "not UTF-8" in (result.checks[0].reason or "")


def test_missing_required_payload_fields_returns_fail(tmp_path):
    projects = ProjectStore(tmp_path / "projects")
    handle = projects.create_project()
    task = TaskStore(projects).create_task(handle.project_id, task_input())
    artifact = create_raw_artifact(projects, handle, task, content=b'{}\n')
    result = DeterministicSchemaValidator(projects).validate(
        handle.project_id, artifact.artifact_id, 1
    )
    assert result.overall is ValidationStatus.FAIL
    assert "G6.8 fields" in (result.checks[0].reason or "")


def test_unknown_artifact_type_returns_fail(tmp_path):
    projects = ProjectStore(tmp_path / "projects")
    handle = projects.create_project()
    task = TaskStore(projects).create_task(handle.project_id, task_input())
    artifact = create_raw_artifact(
        projects,
        handle,
        task,
        content=b'{}\n',
        artifact_type="engineering.unknown.v1",
    )
    result = DeterministicSchemaValidator(projects).validate(
        handle.project_id, artifact.artifact_id, 1
    )
    assert result.overall is ValidationStatus.FAIL
    assert result.checks[0].field == "artifact.type"


def test_task_must_request_schema_validator(tmp_path):
    projects = ProjectStore(tmp_path / "projects")
    handle = projects.create_project()
    task = TaskStore(projects).create_task(
        handle.project_id, task_input(validators=())
    )
    artifact = create_raw_artifact(projects, handle, task, content=b'{}\n')
    with pytest.raises(SchemaValidationConflictError, match="did not request"):
        DeterministicSchemaValidator(projects).validate(
            handle.project_id, artifact.artifact_id, 1
        )


def test_payload_schema_id_mismatch_returns_fail(tmp_path):
    projects, handle, task, artifact = setup_valid(tmp_path)
    content_path = projects.open_project(handle.project_id).layout.project_dir
    # Create a separately hash-valid artifact with one structurally valid payload
    # whose schema_id does not match the producer task contract.
    raw = artifact.payload.to_dict()
    raw["schema_id"] = "other_schema.v1"
    manual = create_raw_artifact(
        projects,
        handle,
        task,
        content=(json.dumps(raw, sort_keys=True, separators=(",", ":")) + "\n").encode(),
    )
    result = DeterministicSchemaValidator(projects).validate(
        handle.project_id, manual.artifact_id, 1
    )
    assert result.overall is ValidationStatus.FAIL
    assert result.checks[0].field == "schema_id"


def test_payload_role_mismatch_returns_fail(tmp_path):
    projects, handle, task, artifact = setup_valid(tmp_path)
    raw = artifact.payload.to_dict()
    raw["role_id"] = "requirements_engineer"
    raw["agent_identity"] = f"requirements_engineer:{task.task_id.value}"
    manual = create_raw_artifact(
        projects,
        handle,
        task,
        content=(json.dumps(raw, sort_keys=True, separators=(",", ":")) + "\n").encode(),
    )
    result = DeterministicSchemaValidator(projects).validate(
        handle.project_id, manual.artifact_id, 1
    )
    assert result.overall is ValidationStatus.FAIL
    assert result.checks[0].field == "role_id"


def test_payload_task_mismatch_returns_fail(tmp_path):
    projects, handle, task, artifact = setup_valid(tmp_path)
    other = TaskStore(projects).create_task(handle.project_id, task_input())
    raw = artifact.payload.to_dict()
    raw["task_id"] = other.task_id.value
    raw["agent_identity"] = f"{task.role}:{other.task_id.value}"
    manual = create_raw_artifact(
        projects,
        handle,
        task,
        content=(json.dumps(raw, sort_keys=True, separators=(",", ":")) + "\n").encode(),
    )
    result = DeterministicSchemaValidator(projects).validate(
        handle.project_id, manual.artifact_id, 1
    )
    assert result.overall is ValidationStatus.FAIL
    assert result.checks[0].field == "task_id"


def test_missing_artifact_is_conflict_not_engineering_fail(tmp_path):
    projects = ProjectStore(tmp_path / "projects")
    handle = projects.create_project()
    with pytest.raises(SchemaValidationConflictError, match="opened safely"):
        DeterministicSchemaValidator(projects).validate(
            handle.project_id, "ART-000001", 1
        )


def test_validation_result_details_are_deterministic(tmp_path):
    projects, handle, task, artifact = setup_valid(tmp_path)
    result = DeterministicSchemaValidator(projects).validate(
        handle.project_id, artifact.record.artifact_id, 1
    )
    details = result.checks[0].to_dict()["details"]
    assert details == {
        "validator_contract_version": "1.0",
        "artifact_type": RESULT_DRAFT_ARTIFACT_TYPE,
        "task_output_schema": task.output_schema,
    }


def test_schema_validator_has_no_lifecycle_transition_api(tmp_path):
    projects = ProjectStore(tmp_path / "projects")
    validator = DeterministicSchemaValidator(projects)
    assert not hasattr(validator, "approve")
    assert not hasattr(validator, "transition")
    assert not hasattr(validator, "persist")


def test_schema_validation_does_not_call_provider(tmp_path):
    projects, handle, _task, artifact = setup_valid(tmp_path)
    # V7.1 accepts only persisted project/artifact inputs; there is no provider
    # object in its constructor or validate() API.
    validator = DeterministicSchemaValidator(projects)
    result = validator.validate(handle.project_id, artifact.record.artifact_id, 1)
    assert result.overall is ValidationStatus.PASS
