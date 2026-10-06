from __future__ import annotations

from dataclasses import dataclass

from ai.agents.instance import AgentInstance, AgentInstanceFactory
from ai.agents.roles import DEFAULT_ROLE_REGISTRY, RoleDefinition, RoleRegistry, RoleRegistryError
from ai.orchestrator.proposal import TASK_PLAN_PROPOSAL_CONTRACT_VERSION, TaskPlanProposal


ORCHESTRATOR_ROLE_CONTRACT_VERSION = "1.0"
ORCHESTRATOR_ROLE_ID = "coordinator"
ORCHESTRATOR_ROLE_TITLE = "Coordinator"
ORCHESTRATOR_OUTPUT_CONTRACT = "TaskPlanProposal"


class OrchestratorRoleError(ValueError):
    """Raised when the Phase-8 orchestrator role boundary cannot be satisfied."""


@dataclass(frozen=True)
class OrchestratorRoleProfile:
    """Immutable O8.2 capability profile for the existing coordinator role.

    The frozen architecture's MVP RoleRegistry already contains ``coordinator``
    and does not define a separate canonical ``orchestrator`` role ID. O8.2
    therefore binds orchestration capability to that existing role instead of
    mutating the G6.1 role namespace.

    This profile grants no validation, approval, persistence, scheduling, task
    execution, provider, or UAV authority. Its only output boundary is the
    already-defined O8.1 :class:`TaskPlanProposal` contract.
    """

    role: RoleDefinition
    proposal_contract_version: str = TASK_PLAN_PROPOSAL_CONTRACT_VERSION

    def __post_init__(self) -> None:
        if not isinstance(self.role, RoleDefinition):
            raise OrchestratorRoleError("role must be RoleDefinition")
        if self.role.role_id != ORCHESTRATOR_ROLE_ID:
            raise OrchestratorRoleError(
                f"orchestrator profile must bind role_id={ORCHESTRATOR_ROLE_ID!r}"
            )
        if self.proposal_contract_version != TASK_PLAN_PROPOSAL_CONTRACT_VERSION:
            raise OrchestratorRoleError(
                "proposal_contract_version must match TaskPlanProposal contract"
            )

    @property
    def role_id(self) -> str:
        return self.role.role_id

    @property
    def output_contract(self) -> str:
        return ORCHESTRATOR_OUTPUT_CONTRACT

    def require_proposal(self, proposal: TaskPlanProposal) -> TaskPlanProposal:
        """Accept only an already-structured O8.1 proposal, without approving it."""
        if not isinstance(proposal, TaskPlanProposal):
            raise OrchestratorRoleError("orchestrator output must be TaskPlanProposal")
        return proposal


def bind_orchestrator_role(
    role_registry: RoleRegistry = DEFAULT_ROLE_REGISTRY,
) -> OrchestratorRoleProfile:
    """Bind the orchestrator capability to the registered coordinator role."""
    if not isinstance(role_registry, RoleRegistry):
        raise TypeError("role_registry must be RoleRegistry")
    try:
        role = role_registry.get(ORCHESTRATOR_ROLE_ID)
    except RoleRegistryError as exc:
        raise OrchestratorRoleError(
            "coordinator role is required for Phase-8 orchestration"
        ) from exc
    return OrchestratorRoleProfile(role=role)


def create_orchestrator_agent(
    *,
    instance_key: str,
    role_registry: RoleRegistry = DEFAULT_ROLE_REGISTRY,
) -> AgentInstance:
    """Create a logical coordinator AgentInstance; never execute or call a provider."""
    profile = bind_orchestrator_role(role_registry)
    return AgentInstanceFactory(role_registry).create(
        instance_key=instance_key,
        role_id=profile.role_id,
    )


DEFAULT_ORCHESTRATOR_ROLE = bind_orchestrator_role()
