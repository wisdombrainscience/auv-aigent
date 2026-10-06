from __future__ import annotations

from dataclasses import dataclass

from ai.agents.instance import AgentInstance, AgentInstanceFactory
from ai.agents.roles import (
    DEFAULT_ROLE_REGISTRY,
    RoleDefinition,
    RoleRegistry,
    RoleRegistryError,
)
from ai.contracts.review_result import REVIEW_RESULT_CONTRACT_VERSION, ReviewResult


REVIEWER_ROLE_CONTRACT_VERSION = "1.0"
REVIEWER_ROLE_ID = "reviewer"
REVIEWER_ROLE_TITLE = "Reviewer"
REVIEWER_OUTPUT_CONTRACT = "ReviewResult"


class ReviewerRoleError(ValueError):
    """Raised when the Phase-9 reviewer capability boundary cannot be satisfied."""


@dataclass(frozen=True)
class ReviewerRoleProfile:
    """Immutable R9.2 capability profile for the existing reviewer role.

    The G6.1 MVP registry already contains ``reviewer``. R9.2 therefore binds
    review capability to that existing logical role rather than adding or
    mutating role IDs.

    This profile is deliberately advisory. It grants no issue allocation or
    persistence, review-task generation, artifact approval/rejection, owner
    routing, rework generation, duplicate suppression, provider execution, or
    UAV authority. Its only output boundary is the already-frozen C1.6/R9.1
    :class:`ReviewResult` contract.
    """

    role: RoleDefinition
    result_contract_version: str = REVIEW_RESULT_CONTRACT_VERSION

    def __post_init__(self) -> None:
        if not isinstance(self.role, RoleDefinition):
            raise ReviewerRoleError("role must be RoleDefinition")
        if self.role.role_id != REVIEWER_ROLE_ID:
            raise ReviewerRoleError(
                f"reviewer profile must bind role_id={REVIEWER_ROLE_ID!r}"
            )
        if self.result_contract_version != REVIEW_RESULT_CONTRACT_VERSION:
            raise ReviewerRoleError(
                "result_contract_version must match ReviewResult contract"
            )

    @property
    def role_id(self) -> str:
        return self.role.role_id

    @property
    def output_contract(self) -> str:
        return REVIEWER_OUTPUT_CONTRACT

    def require_result(self, result: ReviewResult) -> ReviewResult:
        """Accept one structured advisory ReviewResult without mutating it."""
        if not isinstance(result, ReviewResult):
            raise ReviewerRoleError("reviewer output must be ReviewResult")
        return result


def bind_reviewer_role(
    role_registry: RoleRegistry = DEFAULT_ROLE_REGISTRY,
) -> ReviewerRoleProfile:
    """Bind the R9.2 reviewer capability to the registered reviewer role."""
    if not isinstance(role_registry, RoleRegistry):
        raise TypeError("role_registry must be RoleRegistry")
    try:
        role = role_registry.get(REVIEWER_ROLE_ID)
    except RoleRegistryError as exc:
        raise ReviewerRoleError(
            "reviewer role is required for Phase-9 review"
        ) from exc
    return ReviewerRoleProfile(role=role)


def create_reviewer_agent(
    *,
    instance_key: str,
    role_registry: RoleRegistry = DEFAULT_ROLE_REGISTRY,
) -> AgentInstance:
    """Create a logical reviewer AgentInstance; never execute or call a provider."""
    profile = bind_reviewer_role(role_registry)
    return AgentInstanceFactory(role_registry).create(
        instance_key=instance_key,
        role_id=profile.role_id,
    )


DEFAULT_REVIEWER_ROLE = bind_reviewer_role()
