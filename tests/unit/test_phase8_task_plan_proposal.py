from __future__ import annotations

import json

import pytest

from ai.artifacts.schema import ArtifactInputVersion
from ai.contracts.ids import CanonicalId, IdKind
from ai.orchestrator import (
    MAX_PROPOSED_MAX_ATTEMPTS,
    MAX_TASK_PLAN_PROPOSAL_BYTES,
    MAX_TASK_PLAN_PROPOSAL_TASKS,
    TASK_PLAN_PROPOSAL_CONTRACT_VERSION,
    ProposedTask,
    TaskPlanProposal,
    TaskPlanProposalError,
    parse_task_plan_proposal,
    parse_task_plan_proposal_json,
    serialize_task_plan_proposal,
)


def task_id(n: int) -> CanonicalId:
    return CanonicalId.from_sequence(IdKind.TASK, n)


def project_id(n: int = 1) -> CanonicalId:
    return CanonicalId.from_sequence(IdKind.PROJECT, n)


def artifact_ref(n: int = 1, version: int = 1) -> ArtifactInputVersion:
    return ArtifactInputVersion(
        entity_id=CanonicalId.from_sequence(IdKind.ARTIFACT, n),
        version=version,
    )


def proposed_task(
    n: int = 1,
    *,
    role: str = "uav_systems_engineer",
    dependencies: tuple[CanonicalId, ...] = (),
    output_schema: str = "engineering_task.v1",
    validators: tuple[str, ...] = ("schema", "finite_values"),
    max_attempts: int = 3,
    input_artifacts: tuple[ArtifactInputVersion, ...] = (),
) -> ProposedTask:
    return ProposedTask(
        task_id=task_id(n),
        title=f"Task {n}",
        role=role,
        priority=n,
        dependencies=dependencies,
        input_artifacts=input_artifacts,
        output_schema=output_schema,
        validators=validators,
        review_required=True,
        max_attempts=max_attempts,
    )


def proposal(*tasks: ProposedTask) -> TaskPlanProposal:
    return TaskPlanProposal(
        project_id=project_id(),
        requirement_version=2,
        tasks=tasks or (proposed_task(),),
    )


def test_contract_version_is_frozen() -> None:
    assert TASK_PLAN_PROPOSAL_CONTRACT_VERSION == "1.0"


def test_minimal_proposal_roundtrip_dict_and_json() -> None:
    original = proposal(
        proposed_task(1),
        proposed_task(2, dependencies=(task_id(1),), input_artifacts=(artifact_ref(),)),
    )

    parsed = parse_task_plan_proposal(original.to_dict())
    encoded = serialize_task_plan_proposal(original)
    reparsed = parse_task_plan_proposal_json(encoded)

    assert parsed == original
    assert reparsed == original
    assert json.loads(encoded) == original.to_dict()


def test_serialization_is_canonical_and_deterministic() -> None:
    item = proposal(proposed_task(2), proposed_task(1))
    first = serialize_task_plan_proposal(item)
    second = serialize_task_plan_proposal(parse_task_plan_proposal_json(first))
    assert first == second
    assert b", " not in first
    assert b": " not in first


def test_proposed_task_has_no_runtime_state_fields() -> None:
    raw = proposed_task().to_dict()
    assert set(raw) == {
        "task_id",
        "title",
        "role",
        "priority",
        "dependencies",
        "input_artifacts",
        "output_schema",
        "validators",
        "review_required",
        "max_attempts",
    }
    assert "status" not in raw
    assert "attempt" not in raw
    assert "project_id" not in raw


def test_proposal_binds_project_and_requirement_version() -> None:
    item = proposal()
    assert item.project_id.value == "UAV-PROJ-000001"
    assert item.requirement_version == 2


def test_duplicate_task_ids_are_structurally_allowed_for_later_plan_validator() -> None:
    item = proposal(proposed_task(1), proposed_task(1))
    assert [task.task_id.value for task in item.tasks] == ["TASK-000001", "TASK-000001"]


def test_unknown_but_well_formed_role_is_allowed_for_o8_4() -> None:
    item = proposal(proposed_task(role="future_specialist"))
    assert item.tasks[0].role == "future_specialist"


def test_unknown_but_well_formed_schema_is_allowed_for_o8_5() -> None:
    item = proposal(proposed_task(output_schema="hallucinated.schema.v1"))
    assert item.tasks[0].output_schema == "hallucinated.schema.v1"


def test_missing_dependency_target_is_allowed_for_o8_6() -> None:
    item = proposal(proposed_task(1, dependencies=(task_id(999),)))
    assert item.tasks[0].dependencies[0].value == "TASK-000999"


def test_self_dependency_is_allowed_for_later_dag_validation() -> None:
    item = proposal(proposed_task(1, dependencies=(task_id(1),)))
    assert item.tasks[0].dependencies[0] == item.tasks[0].task_id


def test_cycle_is_allowed_for_later_dag_validation() -> None:
    item = proposal(
        proposed_task(1, dependencies=(task_id(2),)),
        proposed_task(2, dependencies=(task_id(1),)),
    )
    assert len(item.tasks) == 2


def test_retry_above_runtime_policy_is_allowed_for_later_plan_validator() -> None:
    item = proposal(proposed_task(max_attempts=4))
    assert item.tasks[0].max_attempts == 4


def test_more_than_frozen_30_tasks_is_structurally_allowed_for_o8_7() -> None:
    tasks = tuple(proposed_task(n) for n in range(1, 32))
    item = proposal(*tasks)
    assert len(item.tasks) == 31


def test_defensive_task_count_bound_rejects_unbounded_payload() -> None:
    tasks = tuple(proposed_task((n % 999_999) + 1) for n in range(MAX_TASK_PLAN_PROPOSAL_TASKS + 1))
    with pytest.raises(TaskPlanProposalError, match="MAX_TASK_PLAN_PROPOSAL_TASKS"):
        proposal(*tasks)


def test_empty_plan_is_not_a_task_plan_proposal() -> None:
    with pytest.raises(TaskPlanProposalError, match="at least one"):
        TaskPlanProposal(project_id=project_id(), requirement_version=1, tasks=())


@pytest.mark.parametrize("raw", [0, True, -1, 1_000_000])
def test_invalid_requirement_version_rejected(raw: object) -> None:
    with pytest.raises(TaskPlanProposalError, match="requirement_version"):
        TaskPlanProposal(project_id=project_id(), requirement_version=raw, tasks=(proposed_task(),))


def test_wrong_project_id_kind_rejected() -> None:
    with pytest.raises(TaskPlanProposalError, match="project_id"):
        TaskPlanProposal(project_id=task_id(1), requirement_version=1, tasks=(proposed_task(),))


@pytest.mark.parametrize("raw", ["", "Reviewer", "bad-role", "1bad", " bad"])
def test_role_syntax_is_structurally_validated(raw: str) -> None:
    with pytest.raises(TaskPlanProposalError, match="role"):
        proposed_task(role=raw)


@pytest.mark.parametrize("raw", ["", " bad", "bad/schema", "9schema"])
def test_output_schema_syntax_is_structurally_validated(raw: str) -> None:
    with pytest.raises(TaskPlanProposalError, match="output_schema"):
        proposed_task(output_schema=raw)


def test_duplicate_dependency_declaration_rejected_as_wire_shape() -> None:
    with pytest.raises(TaskPlanProposalError, match="duplicate TASK IDs"):
        proposed_task(dependencies=(task_id(2), task_id(2)))


def test_duplicate_validator_rejected_as_wire_shape() -> None:
    with pytest.raises(TaskPlanProposalError, match="duplicate validator"):
        proposed_task(validators=("schema", "schema"))


def test_duplicate_input_artifact_rejected_as_wire_shape() -> None:
    ref = artifact_ref()
    with pytest.raises(TaskPlanProposalError, match="duplicate input artifact"):
        proposed_task(input_artifacts=(ref, ref))


@pytest.mark.parametrize("raw", [0, True, -1, MAX_PROPOSED_MAX_ATTEMPTS + 1])
def test_defensive_max_attempts_bound(raw: object) -> None:
    with pytest.raises(TaskPlanProposalError, match="max_attempts"):
        proposed_task(max_attempts=raw)


def test_exact_top_level_keys_required() -> None:
    raw = proposal().to_dict()
    raw["extra"] = 1
    with pytest.raises(TaskPlanProposalError, match="keys do not match"):
        parse_task_plan_proposal(raw)


def test_exact_task_keys_required() -> None:
    raw = proposal().to_dict()
    del raw["tasks"][0]["review_required"]
    with pytest.raises(TaskPlanProposalError, match="keys do not match"):
        parse_task_plan_proposal(raw)


def test_arrays_are_required_on_wire() -> None:
    raw = proposal().to_dict()
    raw["tasks"] = "not-array"
    with pytest.raises(TaskPlanProposalError, match="tasks must be an array"):
        parse_task_plan_proposal(raw)


def test_invalid_input_artifact_wire_value_rejected() -> None:
    raw = proposal().to_dict()
    raw["tasks"][0]["input_artifacts"] = [{"entity_id": "TASK-000001", "version": 1}]
    with pytest.raises(TaskPlanProposalError, match="input_artifacts is invalid"):
        parse_task_plan_proposal(raw)


def test_json_duplicate_key_rejected() -> None:
    raw = b'{"project_id":"UAV-PROJ-000001","requirement_version":1,"requirement_version":2,"tasks":[]}'
    with pytest.raises(TaskPlanProposalError, match="duplicate JSON object key"):
        parse_task_plan_proposal_json(raw)


@pytest.mark.parametrize("constant", ["NaN", "Infinity", "-Infinity"])
def test_non_standard_json_number_rejected(constant: str) -> None:
    raw = (
        '{"project_id":"UAV-PROJ-000001","requirement_version":1,'
        '"tasks":[{"task_id":"TASK-000001","title":"x",'
        '"role":"coordinator","priority":' + constant + ','
        '"dependencies":[],"input_artifacts":[],"output_schema":"engineering_task.v1",'
        '"validators":[],"review_required":false,"max_attempts":1}]}'
    )
    with pytest.raises(TaskPlanProposalError, match="non-standard JSON"):
        parse_task_plan_proposal_json(raw)


def test_invalid_utf8_rejected() -> None:
    with pytest.raises(TaskPlanProposalError, match="valid UTF-8"):
        parse_task_plan_proposal_json(b"\xff")


def test_json_root_must_be_object() -> None:
    with pytest.raises(TaskPlanProposalError, match="root must be an object"):
        parse_task_plan_proposal_json("[]")


def test_serialized_size_bound_is_enforced_before_parse() -> None:
    raw = b" " * (MAX_TASK_PLAN_PROPOSAL_BYTES + 1)
    with pytest.raises(TaskPlanProposalError, match="MAX_TASK_PLAN_PROPOSAL_BYTES"):
        parse_task_plan_proposal_json(raw)


def test_serialize_requires_contract_instance() -> None:
    with pytest.raises(TaskPlanProposalError, match="TaskPlanProposal"):
        serialize_task_plan_proposal({})  # type: ignore[arg-type]


def test_task_order_and_dependency_order_are_preserved() -> None:
    item = proposal(
        proposed_task(3),
        proposed_task(2, dependencies=(task_id(3), task_id(1))),
        proposed_task(1),
    )
    reparsed = parse_task_plan_proposal_json(serialize_task_plan_proposal(item))
    assert [x.task_id.value for x in reparsed.tasks] == ["TASK-000003", "TASK-000002", "TASK-000001"]
    assert [x.value for x in reparsed.tasks[1].dependencies] == ["TASK-000003", "TASK-000001"]


def test_candidate_task_ids_are_format_only_and_do_not_allocate_persistence() -> None:
    first = proposed_task(42)
    second = proposed_task(42)
    assert first.task_id == second.task_id
    assert first.task_id.value == "TASK-000042"
