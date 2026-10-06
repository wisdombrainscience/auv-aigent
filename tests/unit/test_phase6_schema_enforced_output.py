from __future__ import annotations

import json
from pathlib import Path

import pytest

from ai.agents import (
    AGENT_RESULT_OUTPUT_SCHEMA_ID,
    DEFAULT_OUTPUT_SCHEMA_REGISTRY,
    MAX_OUTPUT_SCHEMA_BYTES,
    MAX_OUTPUT_SCHEMA_ID_CHARS,
    SCHEMA_ENFORCED_OUTPUT_CONTRACT_VERSION,
    OutputSchemaConflictError,
    OutputSchemaDefinition,
    OutputSchemaRegistry,
    SchemaEnforcedAgentOutput,
    SchemaEnforcedOutputValidator,
    UnknownOutputSchemaError,
)
from ai.contracts.agent_result import AgentResult
from ai.project.store import ProjectStore
from ai.providers.contracts import GenerateResponse
from ai.providers.model_output import ModelOutputError, ModelOutputReason
from ai.tasks import TaskCreateInput, TaskStore


def task_input(
    title: str = "Engineering output task",
    *,
    role: str = "uav_systems_engineer",
    output_schema: str = AGENT_RESULT_OUTPUT_SCHEMA_ID,
) -> TaskCreateInput:
    return TaskCreateInput(
        title=title,
        role=role,
        priority=100,
        dependencies=(),
        input_artifacts=(),
        output_schema=output_schema,
        validators=("schema",),
        review_required=False,
        max_attempts=3,
    )


def valid_payload() -> dict[str, object]:
    return {
        "status": "PROPOSED",
        "claims": [
            {
                "classification": "AI_PROPOSAL",
                "statement": "Use the bounded engineering proposal.",
            }
        ],
        "assumptions": [],
        "calculations": [],
        "artifacts": [],
        "risks": [],
        "missing_information": [],
        "open_questions": [],
    }


def response(payload: dict[str, object] | str) -> GenerateResponse:
    content = payload if isinstance(payload, str) else json.dumps(
        payload, separators=(",", ":")
    )
    return GenerateResponse(model="local-test-model", content=content, done=True)


def make_project(tmp_path: Path):
    projects = ProjectStore(tmp_path / "remote-uav" / "projects")
    handle = projects.create_project()
    tasks = TaskStore(projects)
    task = tasks.create_task(handle.project_id, task_input())
    return projects, handle, tasks, task


def test_contract_constants_are_explicit_and_bounded() -> None:
    assert SCHEMA_ENFORCED_OUTPUT_CONTRACT_VERSION == "1.0"
    assert AGENT_RESULT_OUTPUT_SCHEMA_ID == "engineering_task.v1"
    assert MAX_OUTPUT_SCHEMA_ID_CHARS == 128
    assert MAX_OUTPUT_SCHEMA_BYTES == 64 * 1024


def test_default_registry_contains_exactly_generic_engineering_schema() -> None:
    assert DEFAULT_OUTPUT_SCHEMA_REGISTRY.schema_ids() == (
        "engineering_task.v1",
    )
    assert DEFAULT_OUTPUT_SCHEMA_REGISTRY.contains("engineering_task.v1")
    assert not DEFAULT_OUTPUT_SCHEMA_REGISTRY.contains("mass_budget.v1")


def test_provider_schema_is_strict_agent_result_envelope(tmp_path: Path) -> None:
    projects, handle, _tasks, task = make_project(tmp_path)
    schema = SchemaEnforcedOutputValidator(projects).provider_schema_for_task(
        handle.project_id, task.task_id
    )

    assert schema["type"] == "object"
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == {
        "status",
        "claims",
        "assumptions",
        "calculations",
        "artifacts",
        "risks",
        "missing_information",
        "open_questions",
    }
    assert schema["properties"]["status"]["enum"] == ["PROPOSED"]
    classifications = schema["properties"]["claims"]["items"]["properties"][
        "classification"
    ]["enum"]
    assert classifications == [
        "USER_FACT",
        "VERIFIED_FACT",
        "USER_REQUIREMENT",
        "ENGINEERING_ASSUMPTION",
        "AI_PROPOSAL",
        "CALCULATED_VALUE",
        "SIMULATION_RESULT",
        "VERIFIER_RESULT",
    ]


def test_provider_schema_snapshot_is_detached(tmp_path: Path) -> None:
    projects, handle, _tasks, task = make_project(tmp_path)
    validator = SchemaEnforcedOutputValidator(projects)
    first = validator.provider_schema_for_task(handle.project_id, task.task_id)
    first["properties"]["status"]["enum"][0] = "PASS"
    second = validator.provider_schema_for_task(handle.project_id, task.task_id)
    assert second["properties"]["status"]["enum"] == ["PROPOSED"]


def test_valid_response_is_bound_to_task_and_agent(tmp_path: Path) -> None:
    projects, handle, _tasks, task = make_project(tmp_path)
    output = SchemaEnforcedOutputValidator(projects).enforce(
        handle.project_id,
        task.task_id,
        response(valid_payload()),
    )

    assert isinstance(output, SchemaEnforcedAgentOutput)
    assert isinstance(output.result, AgentResult)
    assert output.schema_id == "engineering_task.v1"
    assert output.binding.project_id == handle.project_id
    assert output.binding.task_id == task.task_id
    assert output.binding.role_id == "uav_systems_engineer"
    assert output.binding.agent.instance_key == task.task_id.value
    assert output.result.to_dict() == valid_payload()


def test_serialized_output_remains_proposed_not_approved(tmp_path: Path) -> None:
    projects, handle, _tasks, task = make_project(tmp_path)
    output = SchemaEnforcedOutputValidator(projects).enforce(
        handle.project_id, task.task_id, response(valid_payload())
    ).to_dict()
    assert output["result"]["status"] == "PROPOSED"
    assert "approval" not in output
    assert "verdict" not in output


def test_invalid_model_contract_keeps_phase2_model_failure(tmp_path: Path) -> None:
    projects, handle, _tasks, task = make_project(tmp_path)
    payload = valid_payload()
    payload["status"] = "PASS"

    with pytest.raises(ModelOutputError) as caught:
        SchemaEnforcedOutputValidator(projects).enforce(
            handle.project_id, task.task_id, response(payload)
        )

    assert caught.value.reason is ModelOutputReason.CONTRACT_VIOLATION
    failure = caught.value.failure.to_dict()
    assert failure["class"] == "MODEL"
    assert failure["code"] == "MODEL_INVALID_OUTPUT"


def test_duplicate_json_key_keeps_phase2_failure_semantics(tmp_path: Path) -> None:
    projects, handle, _tasks, task = make_project(tmp_path)
    raw = (
        '{"status":"PROPOSED","status":"PROPOSED","claims":[],'
        '"assumptions":[],"calculations":[],"artifacts":[],"risks":[],'
        '"missing_information":[],"open_questions":[]}'
    )
    with pytest.raises(ModelOutputError) as caught:
        SchemaEnforcedOutputValidator(projects).enforce(
            handle.project_id, task.task_id, response(raw)
        )
    assert caught.value.reason is ModelOutputReason.DUPLICATE_JSON_KEY


def test_unknown_task_schema_fails_closed_before_model_parse(tmp_path: Path) -> None:
    projects = ProjectStore(tmp_path / "remote-uav" / "projects")
    handle = projects.create_project()
    task = TaskStore(projects).create_task(
        handle.project_id,
        task_input(output_schema="mass_budget.v1"),
    )
    with pytest.raises(UnknownOutputSchemaError, match="not enabled"):
        SchemaEnforcedOutputValidator(projects).enforce(
            handle.project_id,
            task.task_id,
            response("not-json"),
        )


def test_unknown_schema_lookup_fails_closed(tmp_path: Path) -> None:
    projects = ProjectStore(tmp_path / "remote-uav" / "projects")
    handle = projects.create_project()
    task = TaskStore(projects).create_task(
        handle.project_id,
        task_input(output_schema="review.v1"),
    )
    with pytest.raises(UnknownOutputSchemaError):
        SchemaEnforcedOutputValidator(projects).schema_for_task(
            handle.project_id, task.task_id
        )


def test_unknown_or_reserved_role_fails_mapping_before_output(tmp_path: Path) -> None:
    projects = ProjectStore(tmp_path / "remote-uav" / "projects")
    handle = projects.create_project()
    task = TaskStore(projects).create_task(
        handle.project_id,
        task_input(role="propulsion_power_engineer"),
    )
    with pytest.raises(OutputSchemaConflictError, match="mapping failed"):
        SchemaEnforcedOutputValidator(projects).enforce(
            handle.project_id, task.task_id, response(valid_payload())
        )


def test_missing_task_fails_closed(tmp_path: Path) -> None:
    projects = ProjectStore(tmp_path / "remote-uav" / "projects")
    handle = projects.create_project()
    with pytest.raises(OutputSchemaConflictError, match="mapping failed"):
        SchemaEnforcedOutputValidator(projects).schema_for_task(
            handle.project_id, "TASK-999999"
        )


def test_enforcement_does_not_mutate_task_state_or_attempt(tmp_path: Path) -> None:
    projects, handle, tasks, task = make_project(tmp_path)
    before = tasks.open_task(handle.project_id, task.task_id)
    SchemaEnforcedOutputValidator(projects).enforce(
        handle.project_id, task.task_id, response(valid_payload())
    )
    after = tasks.open_task(handle.project_id, task.task_id)
    assert after == before


def test_enforcement_is_repeatable(tmp_path: Path) -> None:
    projects, handle, _tasks, task = make_project(tmp_path)
    validator = SchemaEnforcedOutputValidator(projects)
    first = validator.enforce(
        handle.project_id, task.task_id, response(valid_payload())
    )
    second = validator.enforce(
        handle.project_id, task.task_id, response(valid_payload())
    )
    assert first == second
    assert first.to_dict() == second.to_dict()


def test_schema_enforcement_survives_restart(tmp_path: Path) -> None:
    projects, handle, _tasks, task = make_project(tmp_path)
    first = SchemaEnforcedOutputValidator(projects).enforce(
        handle.project_id, task.task_id, response(valid_payload())
    )
    restarted_store = ProjectStore(projects.projects_root)
    second = SchemaEnforcedOutputValidator(restarted_store).enforce(
        handle.project_id, task.task_id, response(valid_payload())
    )
    assert second == first


def test_definition_owns_immutable_schema_snapshot() -> None:
    raw = {"type": "object", "properties": {"x": {"type": "string"}}}
    definition = OutputSchemaDefinition(
        schema_id="test.v1",
        contract_name="AgentResult",
        json_schema=raw,
    )
    raw["properties"]["x"]["type"] = "array"
    assert definition.schema_dict()["properties"]["x"]["type"] == "string"


def test_registry_rejects_duplicate_schema_ids() -> None:
    first = OutputSchemaDefinition(
        schema_id="test.v1", contract_name="AgentResult", json_schema={"type": "object"}
    )
    second = OutputSchemaDefinition(
        schema_id="test.v1", contract_name="AgentResult", json_schema={"type": "object"}
    )
    with pytest.raises(OutputSchemaConflictError, match="duplicate"):
        OutputSchemaRegistry((first, second))


@pytest.mark.parametrize(
    "schema_id",
    ["", " leading", "trailing ", "a/b", "_bad", "x" * 129],
)
def test_schema_id_validation_is_fail_closed(schema_id: str) -> None:
    with pytest.raises(OutputSchemaConflictError):
        OutputSchemaDefinition(
            schema_id=schema_id,
            contract_name="AgentResult",
            json_schema={"type": "object"},
        )


def test_schema_rejects_non_json_or_nonfinite_values() -> None:
    with pytest.raises(OutputSchemaConflictError):
        OutputSchemaDefinition(
            schema_id="bad.v1",
            contract_name="AgentResult",
            json_schema={"bad": {1, 2}},
        )
    with pytest.raises(OutputSchemaConflictError):
        OutputSchemaDefinition(
            schema_id="bad.v1",
            contract_name="AgentResult",
            json_schema={"bad": float("nan")},
        )


def test_schema_bound_fails_closed_without_truncation() -> None:
    with pytest.raises(OutputSchemaConflictError, match="MAX_OUTPUT_SCHEMA_BYTES"):
        OutputSchemaDefinition(
            schema_id="huge.v1",
            contract_name="AgentResult",
            json_schema={"description": "x" * (MAX_OUTPUT_SCHEMA_BYTES + 1)},
        )


def test_non_agent_result_contract_is_not_silently_parsed(tmp_path: Path) -> None:
    custom = OutputSchemaRegistry((
        OutputSchemaDefinition(
            schema_id="engineering_task.v1",
            contract_name="DifferentContract",
            json_schema={"type": "object"},
        ),
    ))
    projects, handle, _tasks, task = make_project(tmp_path)
    with pytest.raises(OutputSchemaConflictError, match="AgentResult"):
        SchemaEnforcedOutputValidator(projects, custom).enforce(
            handle.project_id, task.task_id, response(valid_payload())
        )


def test_wrong_response_type_is_programmer_error(tmp_path: Path) -> None:
    projects, handle, _tasks, task = make_project(tmp_path)
    with pytest.raises(TypeError, match="GenerateResponse"):
        SchemaEnforcedOutputValidator(projects).enforce(
            handle.project_id, task.task_id, {"content": "{}"}  # type: ignore[arg-type]
        )


def test_validator_exposes_no_provider_run_persistence_or_artifact_api(tmp_path: Path) -> None:
    projects, _handle, _tasks, _task = make_project(tmp_path)
    validator = SchemaEnforcedOutputValidator(projects)
    for name in (
        "generate",
        "run",
        "execute",
        "provider",
        "model_manager",
        "retry",
        "persist_run",
        "create_manifest",
        "create_artifact",
        "approve",
        "command_uav",
    ):
        assert not hasattr(validator, name)


def test_output_module_has_no_retry_loop_or_uav_imports() -> None:
    import ai.agents.output as module

    text = Path(module.__file__).read_text(encoding="utf-8")
    lowered = text.lower()
    assert "while true" not in lowered
    assert "time.sleep" not in lowered
    for forbidden in ("mavsdk", "px4", "gazebo", "sqlite3"):
        assert forbidden not in lowered
