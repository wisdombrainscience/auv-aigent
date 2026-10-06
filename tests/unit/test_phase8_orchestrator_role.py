from __future__ import annotations

from dataclasses import FrozenInstanceError
import inspect

import pytest

from ai.agents import DEFAULT_ROLE_REGISTRY, MVP_ROLE_IDS, RoleDefinition, RoleRegistry
from ai.orchestrator import (
    DEFAULT_ORCHESTRATOR_ROLE,
    ORCHESTRATOR_OUTPUT_CONTRACT,
    ORCHESTRATOR_ROLE_CONTRACT_VERSION,
    ORCHESTRATOR_ROLE_ID,
    ORCHESTRATOR_ROLE_TITLE,
    TASK_PLAN_PROPOSAL_CONTRACT_VERSION,
    OrchestratorRoleError,
    OrchestratorRoleProfile,
    ProposedTask,
    TaskPlanProposal,
    bind_orchestrator_role,
    create_orchestrator_agent,
)


def _proposal() -> TaskPlanProposal:
    return TaskPlanProposal(
        project_id="UAV-PROJ-000001",
        requirement_version=1,
        tasks=(
            ProposedTask(
                task_id="TASK-000001",
                title="Analyze requirements",
                role="requirements_engineer",
                priority=10,
                dependencies=(),
                input_artifacts=(),
                output_schema="engineering_task.v1",
                validators=("schema",),
                review_required=True,
                max_attempts=3,
            ),
        ),
    )


def test_contract_constants_are_explicit() -> None:
    assert ORCHESTRATOR_ROLE_CONTRACT_VERSION == "1.0"
    assert ORCHESTRATOR_ROLE_ID == "coordinator"
    assert ORCHESTRATOR_ROLE_TITLE == "Coordinator"
    assert ORCHESTRATOR_OUTPUT_CONTRACT == "TaskPlanProposal"


def test_default_profile_binds_existing_coordinator_role() -> None:
    assert DEFAULT_ORCHESTRATOR_ROLE.role is DEFAULT_ROLE_REGISTRY.get("coordinator")
    assert DEFAULT_ORCHESTRATOR_ROLE.role_id == "coordinator"
    assert DEFAULT_ORCHESTRATOR_ROLE.role.title == "Coordinator"
    assert DEFAULT_ORCHESTRATOR_ROLE.output_contract == "TaskPlanProposal"


def test_o8_2_does_not_add_orchestrator_role_id() -> None:
    assert MVP_ROLE_IDS == (
        "coordinator",
        "requirements_engineer",
        "uav_systems_engineer",
        "reviewer",
    )
    assert DEFAULT_ROLE_REGISTRY.role_ids() == (
        "coordinator",
        "requirements_engineer",
        "reviewer",
        "uav_systems_engineer",
    )
    assert DEFAULT_ROLE_REGISTRY.contains("orchestrator") is False


def test_default_registry_remains_four_roles() -> None:
    assert len(DEFAULT_ROLE_REGISTRY.definitions()) == 4


def test_profile_uses_o8_1_proposal_contract_version() -> None:
    assert DEFAULT_ORCHESTRATOR_ROLE.proposal_contract_version == TASK_PLAN_PROPOSAL_CONTRACT_VERSION


def test_require_proposal_accepts_task_plan_proposal_without_mutation() -> None:
    proposal = _proposal()
    assert DEFAULT_ORCHESTRATOR_ROLE.require_proposal(proposal) is proposal


@pytest.mark.parametrize("invalid", [None, {}, [], "proposal", 1, object()])
def test_require_proposal_rejects_non_proposal(invalid: object) -> None:
    with pytest.raises(OrchestratorRoleError, match="TaskPlanProposal"):
        DEFAULT_ORCHESTRATOR_ROLE.require_proposal(invalid)  # type: ignore[arg-type]


def test_profile_rejects_non_role_definition() -> None:
    with pytest.raises(OrchestratorRoleError, match="RoleDefinition"):
        OrchestratorRoleProfile(role="coordinator")  # type: ignore[arg-type]


def test_profile_rejects_non_coordinator_role() -> None:
    with pytest.raises(OrchestratorRoleError, match="coordinator"):
        OrchestratorRoleProfile(role=DEFAULT_ROLE_REGISTRY.get("reviewer"))


def test_profile_rejects_wrong_proposal_contract_version() -> None:
    with pytest.raises(OrchestratorRoleError, match="proposal_contract_version"):
        OrchestratorRoleProfile(
            role=DEFAULT_ROLE_REGISTRY.get("coordinator"),
            proposal_contract_version="999.0",
        )


def test_bind_rejects_non_registry() -> None:
    with pytest.raises(TypeError, match="RoleRegistry"):
        bind_orchestrator_role({})  # type: ignore[arg-type]


def test_bind_fails_closed_if_coordinator_is_missing() -> None:
    registry = RoleRegistry((
        RoleDefinition(
            role_id="reviewer",
            title="Reviewer",
            purpose="Reviews proposals.",
        ),
    ))
    with pytest.raises(OrchestratorRoleError, match="coordinator"):
        bind_orchestrator_role(registry)


def test_bind_accepts_injected_registry_with_coordinator() -> None:
    role = RoleDefinition(
        role_id="coordinator",
        title="Coordinator",
        purpose="Coordinates bounded plan proposals.",
    )
    registry = RoleRegistry((role,))
    profile = bind_orchestrator_role(registry)
    assert profile.role is role


@pytest.mark.parametrize("key", ["ORCH-001", "UAV-PROJ-000001", "plan:UAV-PROJ-000001:v1"])
def test_create_orchestrator_agent_uses_existing_agent_instance_contract(key: str) -> None:
    agent = create_orchestrator_agent(instance_key=key)
    assert agent.role_id == "coordinator"
    assert agent.role is DEFAULT_ROLE_REGISTRY.get("coordinator")
    assert agent.instance_key == key


def test_agent_creation_does_not_mutate_registry() -> None:
    before = DEFAULT_ROLE_REGISTRY.definitions()
    create_orchestrator_agent(instance_key="ORCH-001")
    assert DEFAULT_ROLE_REGISTRY.definitions() == before


def test_profile_is_immutable() -> None:
    with pytest.raises(FrozenInstanceError):
        DEFAULT_ORCHESTRATOR_ROLE.role = DEFAULT_ROLE_REGISTRY.get("reviewer")  # type: ignore[misc]


def test_profile_has_no_plan_validation_authority() -> None:
    forbidden = (
        "validate_plan", "approve_plan", "approve", "persist", "persist_dag",
        "create_task", "enqueue", "schedule", "execute", "run", "dispatch",
    )
    for name in forbidden:
        assert not hasattr(DEFAULT_ORCHESTRATOR_ROLE, name)


def test_profile_has_no_provider_or_uav_authority() -> None:
    forbidden = (
        "generate", "invoke", "provider", "model", "command_uav", "mavsdk",
        "px4", "gazebo", "verify_simulation",
    )
    for name in forbidden:
        assert not hasattr(DEFAULT_ORCHESTRATOR_ROLE, name)


def test_role_module_imports_no_provider_or_uav_runtime() -> None:
    import ai.orchestrator.role as role_module
    source = inspect.getsource(role_module)
    import_lines = tuple(
        line.strip() for line in source.splitlines()
        if line.lstrip().startswith(("import ", "from "))
    )
    joined = "\n".join(import_lines).lower()
    assert "ai.providers" not in joined
    assert "mavsdk" not in joined
    assert "px4" not in joined
    assert "gazebo" not in joined


def test_role_module_does_not_import_project_or_task_persistence() -> None:
    import ai.orchestrator.role as role_module
    source = inspect.getsource(role_module)
    import_lines = tuple(
        line.strip() for line in source.splitlines()
        if line.lstrip().startswith(("import ", "from "))
    )
    joined = "\n".join(import_lines).lower()
    assert "ai.project" not in joined
    assert "ai.tasks.store" not in joined
    assert "sqlite" not in joined


def test_profile_acceptance_does_not_validate_semantic_plan_rules() -> None:
    # O8.2 must not steal O8.3+ responsibilities. O8.1 proposal may contain
    # a well-formed unknown role and O8.2 still only checks output type.
    proposal = TaskPlanProposal(
        project_id="UAV-PROJ-000001",
        requirement_version=1,
        tasks=(
            ProposedTask(
                task_id="TASK-000001",
                title="Deferred semantic validation",
                role="unknown_but_well_formed_role",
                priority=0,
                dependencies=("TASK-000999",),
                input_artifacts=(),
                output_schema="unknown.schema.v1",
                validators=("schema",),
                review_required=True,
                max_attempts=4,
            ),
        ),
    )
    assert DEFAULT_ORCHESTRATOR_ROLE.require_proposal(proposal) is proposal


def test_create_agent_has_no_runtime_state() -> None:
    agent = create_orchestrator_agent(instance_key="ORCH-001")
    for name in (
        "task", "task_id", "context", "prompt", "memory", "tools", "provider",
        "model", "result", "artifact", "status", "run_id",
    ):
        assert not hasattr(agent, name)
