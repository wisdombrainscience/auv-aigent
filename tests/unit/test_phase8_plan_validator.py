from __future__ import annotations

from dataclasses import FrozenInstanceError
import inspect

import pytest

from ai.agents.output import (
    DEFAULT_OUTPUT_SCHEMA_REGISTRY,
    OutputSchemaDefinition,
    OutputSchemaRegistry,
)
from ai.agents.roles import RoleDefinition, RoleRegistry
from ai.contracts.validation_result import ValidationStatus
from ai.orchestrator import (
    DEFAULT_PLAN_VALIDATOR,
    MAX_DEPENDENCY_DEPTH,
    MAX_TASKS_PER_PLAN,
    PLAN_CHECK_DAG_ACYCLIC,
    PLAN_CHECK_DEPENDENCY_DEPTH_BUDGET,
    PLAN_CHECK_DEPENDENCY_EXISTENCE,
    PLAN_CHECK_KNOWN_OUTPUT_SCHEMAS,
    PLAN_CHECK_KNOWN_ROLES,
    PLAN_CHECK_NO_SELF_DEPENDENCIES,
    PLAN_CHECK_RETRY_BUDGET,
    PLAN_CHECK_RUNTIME_RESOURCE_BUDGET,
    PLAN_CHECK_TASK_COUNT_BUDGET,
    PLAN_CHECK_UNIQUE_TASK_IDS,
    PLAN_VALIDATOR_CHECK_IDS,
    PLAN_VALIDATOR_CONTRACT_VERSION,
    PlanValidator,
    PlanValidatorError,
    ProposedTask,
    TaskPlanProposal,
    validate_task_plan_proposal,
)


def _task(
    task_id: str,
    *,
    role: str = "requirements_engineer",
    output_schema: str = "engineering_task.v1",
    dependencies: tuple[str, ...] = (),
    max_attempts: int = 3,
) -> ProposedTask:
    return ProposedTask(
        task_id=task_id,
        title=f"Plan candidate {task_id}",
        role=role,
        priority=10,
        dependencies=dependencies,
        input_artifacts=(),
        output_schema=output_schema,
        validators=("schema",),
        review_required=True,
        max_attempts=max_attempts,
    )


def _proposal(*tasks: ProposedTask) -> TaskPlanProposal:
    return TaskPlanProposal(
        project_id="UAV-PROJ-000001",
        requirement_version=1,
        tasks=tasks,
    )


def _by_id(result, check_id: str):
    return next(check for check in result.checks if check.check_id == check_id)


def test_contract_constants_are_explicit() -> None:
    assert PLAN_VALIDATOR_CONTRACT_VERSION == "1.0"
    assert PLAN_CHECK_UNIQUE_TASK_IDS == "plan.unique_task_ids"
    assert PLAN_CHECK_KNOWN_ROLES == "plan.known_roles"
    assert PLAN_CHECK_KNOWN_OUTPUT_SCHEMAS == "plan.known_output_schemas"
    assert PLAN_CHECK_DEPENDENCY_EXISTENCE == "plan.dependency_existence"
    assert PLAN_CHECK_NO_SELF_DEPENDENCIES == "plan.no_self_dependencies"
    assert PLAN_CHECK_DAG_ACYCLIC == "plan.dag_acyclic"
    assert PLAN_CHECK_TASK_COUNT_BUDGET == "plan.task_count_budget"
    assert PLAN_CHECK_DEPENDENCY_DEPTH_BUDGET == "plan.dependency_depth_budget"
    assert PLAN_CHECK_RETRY_BUDGET == "plan.retry_budget"
    assert PLAN_CHECK_RUNTIME_RESOURCE_BUDGET == "plan.runtime_resource_budget"
    assert MAX_TASKS_PER_PLAN == 30
    assert MAX_DEPENDENCY_DEPTH == 12
    assert PLAN_VALIDATOR_CHECK_IDS == (
        "plan.unique_task_ids",
        "plan.known_roles",
        "plan.known_output_schemas",
        "plan.dependency_existence",
        "plan.no_self_dependencies",
        "plan.dag_acyclic",
        "plan.task_count_budget",
        "plan.dependency_depth_budget",
        "plan.retry_budget",
        "plan.runtime_resource_budget",
    )


def test_default_validator_uses_frozen_default_role_registry() -> None:
    assert isinstance(DEFAULT_PLAN_VALIDATOR, PlanValidator)
    assert DEFAULT_PLAN_VALIDATOR.contract_version == "1.0"
    assert DEFAULT_PLAN_VALIDATOR.check_ids == PLAN_VALIDATOR_CHECK_IDS
    assert DEFAULT_PLAN_VALIDATOR.role_registry.role_ids() == (
        "coordinator",
        "requirements_engineer",
        "reviewer",
        "uav_systems_engineer",
    )
    assert DEFAULT_PLAN_VALIDATOR.output_schema_registry.schema_ids() == (
        "engineering_task.v1",
    )


def test_unique_task_ids_known_roles_and_known_schemas_pass() -> None:
    result = DEFAULT_PLAN_VALIDATOR.validate(
        _proposal(
            _task("TASK-000001", role="requirements_engineer"),
            _task("TASK-000002", role="uav_systems_engineer"),
            _task("TASK-000003", role="reviewer"),
            _task("TASK-000004", role="coordinator"),
        )
    )
    assert result.overall is ValidationStatus.PASS
    assert tuple(check.check_id for check in result.checks) == PLAN_VALIDATOR_CHECK_IDS
    roles = _by_id(result, PLAN_CHECK_KNOWN_ROLES)
    assert roles.status is ValidationStatus.PASS
    assert roles.field == "tasks[].role"
    assert roles.reason is None
    assert roles.details["registered_role_count"] == 4
    assert roles.details["registered_role_ids"] == (
        "coordinator",
        "requirements_engineer",
        "reviewer",
        "uav_systems_engineer",
    )
    assert roles.details["unknown_roles"] == ()
    assert roles.details["unknown_assignments"] == ()
    schemas = _by_id(result, PLAN_CHECK_KNOWN_OUTPUT_SCHEMAS)
    assert schemas.status is ValidationStatus.PASS
    assert schemas.field == "tasks[].output_schema"
    assert schemas.reason is None
    assert schemas.details["registered_schema_count"] == 1
    assert schemas.details["registered_schema_ids"] == ("engineering_task.v1",)
    assert schemas.details["unknown_schemas"] == ()
    assert schemas.details["unknown_assignments"] == ()


def test_duplicate_task_ids_fail_without_hiding_role_check() -> None:
    result = DEFAULT_PLAN_VALIDATOR.validate(
        _proposal(_task("TASK-000001"), _task("TASK-000001"))
    )
    assert result.overall is ValidationStatus.FAIL
    unique = _by_id(result, PLAN_CHECK_UNIQUE_TASK_IDS)
    roles = _by_id(result, PLAN_CHECK_KNOWN_ROLES)
    schemas = _by_id(result, PLAN_CHECK_KNOWN_OUTPUT_SCHEMAS)
    assert unique.status is ValidationStatus.FAIL
    assert unique.details["duplicate_task_ids"] == ("TASK-000001",)
    assert roles.status is ValidationStatus.PASS
    assert schemas.status is ValidationStatus.PASS


def test_unknown_role_fails_o8_4() -> None:
    result = DEFAULT_PLAN_VALIDATOR.validate(
        _proposal(_task("TASK-000001", role="future_specialist"))
    )
    assert result.overall is ValidationStatus.FAIL
    check = _by_id(result, PLAN_CHECK_KNOWN_ROLES)
    assert check.status is ValidationStatus.FAIL
    assert check.field == "tasks[].role"
    assert check.reason == (
        "task plan proposal contains roles not registered in RoleRegistry"
    )
    assert check.details["unknown_roles"] == ("future_specialist",)
    assert check.details["unknown_assignments"] == (
        {"task_id": "TASK-000001", "role": "future_specialist"},
    )


def test_reserved_specialized_role_is_not_silently_enabled() -> None:
    result = DEFAULT_PLAN_VALIDATOR.validate(
        _proposal(_task("TASK-000001", role="propulsion_power_engineer"))
    )
    check = _by_id(result, PLAN_CHECK_KNOWN_ROLES)
    assert check.status is ValidationStatus.FAIL
    assert check.details["unknown_roles"] == ("propulsion_power_engineer",)


def test_orchestrator_is_not_a_new_role_id() -> None:
    result = DEFAULT_PLAN_VALIDATOR.validate(
        _proposal(_task("TASK-000001", role="orchestrator"))
    )
    check = _by_id(result, PLAN_CHECK_KNOWN_ROLES)
    assert check.status is ValidationStatus.FAIL
    assert check.details["unknown_roles"] == ("orchestrator",)


def test_unknown_roles_are_deduplicated_in_first_occurrence_order() -> None:
    result = DEFAULT_PLAN_VALIDATOR.validate(
        _proposal(
            _task("TASK-000001", role="future_b"),
            _task("TASK-000002", role="future_a"),
            _task("TASK-000003", role="future_b"),
            _task("TASK-000004", role="future_c"),
        )
    )
    check = _by_id(result, PLAN_CHECK_KNOWN_ROLES)
    assert check.details["unknown_roles"] == (
        "future_b",
        "future_a",
        "future_c",
    )
    assert check.details["unknown_assignments"] == (
        {"task_id": "TASK-000001", "role": "future_b"},
        {"task_id": "TASK-000002", "role": "future_a"},
        {"task_id": "TASK-000003", "role": "future_b"},
        {"task_id": "TASK-000004", "role": "future_c"},
    )


def test_duplicate_ids_and_unknown_roles_both_report_failures() -> None:
    result = DEFAULT_PLAN_VALIDATOR.validate(
        _proposal(
            _task("TASK-000001", role="future_specialist"),
            _task("TASK-000001", role="requirements_engineer"),
        )
    )
    assert result.overall is ValidationStatus.FAIL
    assert _by_id(result, PLAN_CHECK_UNIQUE_TASK_IDS).status is ValidationStatus.FAIL
    assert _by_id(result, PLAN_CHECK_KNOWN_ROLES).status is ValidationStatus.FAIL


def test_custom_role_registry_is_honored_without_mutating_default_registry() -> None:
    custom = RoleRegistry(
        (
            RoleDefinition(
                role_id="future_specialist",
                title="Future Specialist",
                purpose="Test-only bounded role registry entry.",
            ),
        )
    )
    validator = PlanValidator(role_registry=custom)
    result = validator.validate(
        _proposal(_task("TASK-000001", role="future_specialist"))
    )
    assert result.overall is ValidationStatus.PASS
    assert DEFAULT_PLAN_VALIDATOR.role_registry.contains("future_specialist") is False


def test_non_role_registry_fails_closed() -> None:
    with pytest.raises(PlanValidatorError, match="role_registry"):
        PlanValidator(role_registry=object())  # type: ignore[arg-type]


def test_validation_is_deterministic_for_same_proposal() -> None:
    proposal = _proposal(_task("TASK-000001", role="future_specialist"))
    first = DEFAULT_PLAN_VALIDATOR.validate(proposal).to_dict()
    second = DEFAULT_PLAN_VALIDATOR.validate(proposal).to_dict()
    assert first == second


def test_validation_does_not_mutate_proposal_or_registry() -> None:
    proposal = _proposal(_task("TASK-000001"))
    before = proposal.to_dict()
    role_ids = DEFAULT_PLAN_VALIDATOR.role_registry.role_ids()
    schema_ids = DEFAULT_PLAN_VALIDATOR.output_schema_registry.schema_ids()
    DEFAULT_PLAN_VALIDATOR.validate(proposal)
    assert proposal.to_dict() == before
    assert DEFAULT_PLAN_VALIDATOR.role_registry.role_ids() == role_ids
    assert DEFAULT_PLAN_VALIDATOR.output_schema_registry.schema_ids() == schema_ids


def test_unknown_schema_fails_o8_5() -> None:
    result = DEFAULT_PLAN_VALIDATOR.validate(
        _proposal(_task("TASK-000001", output_schema="future.schema.v1"))
    )
    assert result.overall is ValidationStatus.FAIL
    check = _by_id(result, PLAN_CHECK_KNOWN_OUTPUT_SCHEMAS)
    assert check.status is ValidationStatus.FAIL
    assert check.field == "tasks[].output_schema"
    assert check.reason == (
        "task plan proposal contains output schemas not registered "
        "in OutputSchemaRegistry"
    )
    assert check.details["registered_schema_count"] == 1
    assert check.details["registered_schema_ids"] == ("engineering_task.v1",)
    assert check.details["unknown_schemas"] == ("future.schema.v1",)
    assert check.details["unknown_assignments"] == (
        {"task_id": "TASK-000001", "output_schema": "future.schema.v1"},
    )


def test_unknown_schemas_are_deduplicated_in_first_occurrence_order() -> None:
    result = DEFAULT_PLAN_VALIDATOR.validate(
        _proposal(
            _task("TASK-000001", output_schema="future.b.v1"),
            _task("TASK-000002", output_schema="future.a.v1"),
            _task("TASK-000003", output_schema="future.b.v1"),
            _task("TASK-000004", output_schema="future.c.v1"),
        )
    )
    check = _by_id(result, PLAN_CHECK_KNOWN_OUTPUT_SCHEMAS)
    assert check.details["unknown_schemas"] == (
        "future.b.v1", "future.a.v1", "future.c.v1"
    )
    assert check.details["unknown_assignments"] == (
        {"task_id": "TASK-000001", "output_schema": "future.b.v1"},
        {"task_id": "TASK-000002", "output_schema": "future.a.v1"},
        {"task_id": "TASK-000003", "output_schema": "future.b.v1"},
        {"task_id": "TASK-000004", "output_schema": "future.c.v1"},
    )


def test_custom_output_schema_registry_is_honored_without_mutating_default() -> None:
    base = DEFAULT_OUTPUT_SCHEMA_REGISTRY.get("engineering_task.v1")
    custom = OutputSchemaRegistry(
        (
            OutputSchemaDefinition(
                schema_id="future.schema.v1",
                contract_name=base.contract_name,
                json_schema=base.schema_dict(),
            ),
        )
    )
    validator = PlanValidator(output_schema_registry=custom)
    result = validator.validate(
        _proposal(_task("TASK-000001", output_schema="future.schema.v1"))
    )
    assert result.overall is ValidationStatus.PASS
    assert DEFAULT_PLAN_VALIDATOR.output_schema_registry.contains("future.schema.v1") is False


def test_non_output_schema_registry_fails_closed() -> None:
    with pytest.raises(PlanValidatorError, match="output_schema_registry"):
        PlanValidator(output_schema_registry=object())  # type: ignore[arg-type]


def test_valid_dependency_chain_passes_o8_6() -> None:
    result = DEFAULT_PLAN_VALIDATOR.validate(
        _proposal(
            _task("TASK-000001"),
            _task("TASK-000002", dependencies=("TASK-000001",)),
            _task("TASK-000003", dependencies=("TASK-000002",)),
        )
    )
    assert result.overall is ValidationStatus.PASS
    existence = _by_id(result, PLAN_CHECK_DEPENDENCY_EXISTENCE)
    self_check = _by_id(result, PLAN_CHECK_NO_SELF_DEPENDENCIES)
    dag = _by_id(result, PLAN_CHECK_DAG_ACYCLIC)
    assert existence.status is ValidationStatus.PASS
    assert existence.details["edge_count"] == 2
    assert existence.details["missing_dependencies"] == ()
    assert self_check.status is ValidationStatus.PASS
    assert self_check.details["self_dependencies"] == ()
    assert dag.status is ValidationStatus.PASS
    assert dag.details["cycle"] == ()


def test_missing_dependency_fails_existence_and_blocks_dag_proof() -> None:
    result = DEFAULT_PLAN_VALIDATOR.validate(
        _proposal(_task("TASK-000001", dependencies=("TASK-000999",)))
    )
    assert result.overall is ValidationStatus.FAIL
    existence = _by_id(result, PLAN_CHECK_DEPENDENCY_EXISTENCE)
    dag = _by_id(result, PLAN_CHECK_DAG_ACYCLIC)
    assert existence.status is ValidationStatus.FAIL
    assert existence.details["missing_dependency_ids"] == ("TASK-000999",)
    assert existence.details["missing_dependencies"] == (
        {"task_id": "TASK-000001", "dependency_task_id": "TASK-000999"},
    )
    assert dag.status is ValidationStatus.FAIL
    assert dag.details["blocked_by_missing_dependencies"] == (
        {"task_id": "TASK-000001", "dependency_task_id": "TASK-000999"},
    )


def test_missing_dependency_evidence_is_stable_and_deduplicated() -> None:
    result = DEFAULT_PLAN_VALIDATOR.validate(
        _proposal(
            _task("TASK-000001", dependencies=("TASK-000900", "TASK-000901")),
            _task("TASK-000002", dependencies=("TASK-000901",)),
        )
    )
    check = _by_id(result, PLAN_CHECK_DEPENDENCY_EXISTENCE)
    assert check.details["missing_dependency_ids"] == ("TASK-000900", "TASK-000901")
    assert check.details["missing_dependencies"] == (
        {"task_id": "TASK-000001", "dependency_task_id": "TASK-000900"},
        {"task_id": "TASK-000001", "dependency_task_id": "TASK-000901"},
        {"task_id": "TASK-000002", "dependency_task_id": "TASK-000901"},
    )


def test_self_dependency_fails_self_check_and_is_a_cycle() -> None:
    result = DEFAULT_PLAN_VALIDATOR.validate(
        _proposal(_task("TASK-000001", dependencies=("TASK-000001",)))
    )
    assert result.overall is ValidationStatus.FAIL
    assert _by_id(result, PLAN_CHECK_DEPENDENCY_EXISTENCE).status is ValidationStatus.PASS
    self_check = _by_id(result, PLAN_CHECK_NO_SELF_DEPENDENCIES)
    dag = _by_id(result, PLAN_CHECK_DAG_ACYCLIC)
    assert self_check.status is ValidationStatus.FAIL
    assert self_check.details["self_dependencies"] == (
        {"task_id": "TASK-000001", "dependency_task_id": "TASK-000001"},
    )
    assert dag.status is ValidationStatus.FAIL
    assert dag.details["cycle"] == ("TASK-000001", "TASK-000001")


def test_longer_cycle_fails_with_stable_t5_4_witness() -> None:
    proposal = _proposal(
        _task("TASK-000003", dependencies=("TASK-000001",)),
        _task("TASK-000001", dependencies=("TASK-000002",)),
        _task("TASK-000002", dependencies=("TASK-000003",)),
    )
    first = DEFAULT_PLAN_VALIDATOR.validate(proposal)
    second = DEFAULT_PLAN_VALIDATOR.validate(proposal)
    dag = _by_id(first, PLAN_CHECK_DAG_ACYCLIC)
    assert dag.status is ValidationStatus.FAIL
    assert dag.details["cycle"] == (
        "TASK-000001", "TASK-000002", "TASK-000003", "TASK-000001"
    )
    assert first.to_dict() == second.to_dict()


def test_duplicate_task_ids_block_dag_acyclicity_fail_closed() -> None:
    result = DEFAULT_PLAN_VALIDATOR.validate(
        _proposal(_task("TASK-000001"), _task("TASK-000001"))
    )
    dag = _by_id(result, PLAN_CHECK_DAG_ACYCLIC)
    assert dag.status is ValidationStatus.FAIL
    assert dag.details["blocked_by_duplicate_task_ids"] == ("TASK-000001",)


def test_retry_budget_rejects_attempts_above_frozen_limit() -> None:
    result = DEFAULT_PLAN_VALIDATOR.validate(
        _proposal(_task("TASK-000001", max_attempts=4))
    )
    assert result.overall is ValidationStatus.FAIL
    check = _by_id(result, PLAN_CHECK_RETRY_BUDGET)
    assert check.status is ValidationStatus.FAIL
    assert check.details["max_attempts_per_task"] == 3
    assert check.details["over_budget_tasks"] == (
        {"task_id": "TASK-000001", "max_attempts": 4},
    )


def test_retry_budget_accepts_attempts_at_frozen_limit() -> None:
    result = DEFAULT_PLAN_VALIDATOR.validate(
        _proposal(_task("TASK-000001", max_attempts=3))
    )
    check = _by_id(result, PLAN_CHECK_RETRY_BUDGET)
    assert check.status is ValidationStatus.PASS
    assert check.details["over_budget_tasks"] == ()


def test_task_count_budget_rejects_31_tasks() -> None:
    tasks = tuple(_task(f"TASK-{index:06d}") for index in range(1, 32))
    result = DEFAULT_PLAN_VALIDATOR.validate(_proposal(*tasks))
    assert result.overall is ValidationStatus.FAIL
    check = _by_id(result, PLAN_CHECK_TASK_COUNT_BUDGET)
    assert check.status is ValidationStatus.FAIL
    assert check.details == {
        "task_count": 31,
        "max_tasks_per_plan": 30,
        "over_budget_by": 1,
    }


def test_task_count_budget_accepts_exactly_30_tasks() -> None:
    tasks = tuple(_task(f"TASK-{index:06d}") for index in range(1, 31))
    result = DEFAULT_PLAN_VALIDATOR.validate(_proposal(*tasks))
    check = _by_id(result, PLAN_CHECK_TASK_COUNT_BUDGET)
    assert check.status is ValidationStatus.PASS
    assert check.details["task_count"] == 30


def _chain(task_count: int) -> TaskPlanProposal:
    tasks = []
    for index in range(1, task_count + 1):
        task_id = f"TASK-{index:06d}"
        deps = () if index == 1 else (f"TASK-{index - 1:06d}",)
        tasks.append(_task(task_id, dependencies=deps))
    return _proposal(*tasks)


def test_dependency_depth_budget_accepts_12_edges() -> None:
    result = DEFAULT_PLAN_VALIDATOR.validate(_chain(13))
    check = _by_id(result, PLAN_CHECK_DEPENDENCY_DEPTH_BUDGET)
    assert check.status is ValidationStatus.PASS
    assert check.details["observed_dependency_depth"] == 12
    assert check.details["deepest_path"][0] == "TASK-000001"
    assert check.details["deepest_path"][-1] == "TASK-000013"
    assert len(check.details["deepest_path"]) == 13


def test_dependency_depth_budget_rejects_13_edges() -> None:
    result = DEFAULT_PLAN_VALIDATOR.validate(_chain(14))
    assert result.overall is ValidationStatus.FAIL
    check = _by_id(result, PLAN_CHECK_DEPENDENCY_DEPTH_BUDGET)
    assert check.status is ValidationStatus.FAIL
    assert check.details["observed_dependency_depth"] == 13
    assert check.details["max_dependency_depth"] == 12


def test_dependency_depth_chooses_stable_lexicographic_witness() -> None:
    proposal = _proposal(
        _task("TASK-000001"),
        _task("TASK-000002"),
        _task("TASK-000003", dependencies=("TASK-000001",)),
        _task("TASK-000004", dependencies=("TASK-000002",)),
    )
    check = _by_id(
        DEFAULT_PLAN_VALIDATOR.validate(proposal),
        PLAN_CHECK_DEPENDENCY_DEPTH_BUDGET,
    )
    assert check.details["observed_dependency_depth"] == 1
    assert check.details["deepest_path"] == ("TASK-000001", "TASK-000003")


def test_dependency_depth_fails_closed_when_graph_is_invalid() -> None:
    missing = DEFAULT_PLAN_VALIDATOR.validate(
        _proposal(_task("TASK-000001", dependencies=("TASK-000999",)))
    )
    missing_check = _by_id(missing, PLAN_CHECK_DEPENDENCY_DEPTH_BUDGET)
    assert missing_check.status is ValidationStatus.FAIL
    assert missing_check.details["observed_dependency_depth"] is None
    assert missing_check.details["blocked_by_missing_dependencies"] == (
        {"task_id": "TASK-000001", "dependency_task_id": "TASK-000999"},
    )

    cycle = DEFAULT_PLAN_VALIDATOR.validate(
        _proposal(
            _task("TASK-000001", dependencies=("TASK-000002",)),
            _task("TASK-000002", dependencies=("TASK-000001",)),
        )
    )
    cycle_check = _by_id(cycle, PLAN_CHECK_DEPENDENCY_DEPTH_BUDGET)
    assert cycle_check.status is ValidationStatus.FAIL
    assert cycle_check.details["blocked_by_cycle"] == (
        "TASK-000001", "TASK-000002", "TASK-000001"
    )


def test_runtime_resource_budget_matches_frozen_limits() -> None:
    result = DEFAULT_PLAN_VALIDATOR.validate(_proposal(_task("TASK-000001")))
    check = _by_id(result, PLAN_CHECK_RUNTIME_RESOURCE_BUDGET)
    assert check.status is ValidationStatus.PASS
    assert check.details["observed"] == {
        "max_parallel_agents": 1,
        "max_loaded_models": 1,
        "task_timeout_s": 300.0,
    }
    assert check.details["expected"] == check.details["observed"]
    assert check.details["mismatches"] == {}
    assert check.details["proposal_resource_overrides"] is False


@pytest.mark.parametrize("invalid", [None, {}, [], "plan", 1, object()])
def test_validate_rejects_non_proposal(invalid: object) -> None:
    with pytest.raises(PlanValidatorError, match="TaskPlanProposal"):
        DEFAULT_PLAN_VALIDATOR.validate(invalid)  # type: ignore[arg-type]


def test_helper_uses_default_validator() -> None:
    proposal = _proposal(_task("TASK-000001"))
    assert validate_task_plan_proposal(proposal).to_dict() == (
        DEFAULT_PLAN_VALIDATOR.validate(proposal).to_dict()
    )


def test_helper_accepts_explicit_plan_validator() -> None:
    proposal = _proposal(_task("TASK-000001"))
    validator = PlanValidator()
    result = validate_task_plan_proposal(proposal, validator=validator)
    assert result.overall is ValidationStatus.PASS


def test_helper_rejects_non_validator() -> None:
    proposal = _proposal(_task("TASK-000001"))
    with pytest.raises(TypeError, match="PlanValidator"):
        validate_task_plan_proposal(proposal, validator=object())  # type: ignore[arg-type]


def test_wrong_contract_version_fails_closed() -> None:
    with pytest.raises(PlanValidatorError, match="contract_version"):
        PlanValidator(contract_version="999.0")


def test_validator_is_immutable() -> None:
    with pytest.raises(FrozenInstanceError):
        DEFAULT_PLAN_VALIDATOR.contract_version = "2.0"  # type: ignore[misc]


def test_result_uses_existing_c1_7_contract() -> None:
    result = DEFAULT_PLAN_VALIDATOR.validate(_proposal(_task("TASK-000001")))
    reopened = type(result).from_dict(result.to_dict())
    assert reopened.to_dict() == result.to_dict()


def test_plan_validator_has_no_approval_or_persistence_authority() -> None:
    forbidden = (
        "approve", "approve_plan", "persist", "persist_dag", "create_task",
        "enqueue", "schedule", "execute", "run", "dispatch",
    )
    for name in forbidden:
        assert not hasattr(DEFAULT_PLAN_VALIDATOR, name)


def test_validator_module_imports_role_and_output_schema_registries() -> None:
    import ai.orchestrator.validator as module

    source = inspect.getsource(module)
    import_lines = tuple(
        line.strip()
        for line in source.splitlines()
        if line.lstrip().startswith(("import ", "from "))
    )
    joined = "\n".join(import_lines)
    assert "ai.agents.roles" in joined
    assert "ai.agents.output" in joined
    assert "OutputSchemaRegistry" in source
    assert "ArtifactSchemaRegistry" not in joined


def test_validator_reuses_t5_4_cycle_semantics_and_frozen_resource_runtime() -> None:
    import ai.orchestrator.validator as module

    source = inspect.getsource(module)
    import_lines = tuple(
        line.strip()
        for line in source.splitlines()
        if line.lstrip().startswith(("import ", "from "))
    )
    joined = "\n".join(import_lines)
    assert "from ai.tasks.dag import _find_cycle" in joined
    assert "ai.tasks.concurrency" in joined
    assert "ai.model_manager" in joined
    assert "ai.tasks.lifecycle" in joined
    assert "ai.tasks.schema" in joined


def test_validator_module_imports_no_provider_or_uav_runtime() -> None:
    import ai.orchestrator.validator as module

    source = inspect.getsource(module)
    import_lines = tuple(
        line.strip()
        for line in source.splitlines()
        if line.lstrip().startswith(("import ", "from "))
    )
    joined = "\n".join(import_lines).lower()
    assert "ai.providers" not in joined
    assert "mavsdk" not in joined
    assert "px4" not in joined
    assert "gazebo" not in joined


def test_runtime_resource_budget_fails_closed_on_policy_drift(monkeypatch) -> None:
    import ai.orchestrator.validator as module

    monkeypatch.setattr(module, "MAX_LOADED_MODELS", 2)
    result = module.DEFAULT_PLAN_VALIDATOR.validate(
        _proposal(_task("TASK-000001"))
    )
    check = _by_id(result, PLAN_CHECK_RUNTIME_RESOURCE_BUDGET)
    assert check.status is ValidationStatus.FAIL
    assert check.details["mismatches"] == {
        "max_loaded_models": {"observed": 2, "expected": 1},
    }
