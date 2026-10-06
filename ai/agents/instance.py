from __future__ import annotations

from dataclasses import dataclass
import re

from ai.agents.roles import (
    DEFAULT_ROLE_REGISTRY,
    RoleDefinition,
    RoleRegistry,
    RoleRegistryError,
    UnknownRoleError,
)


AGENT_INSTANCE_CONTRACT_VERSION = "1.0"
MAX_AGENT_INSTANCE_KEY_CHARS = 128

# Intentionally accepts canonical TASK IDs such as TASK-000001 so G6.3 can use
# a validated task identity as the logical instance key without inventing a new
# canonical ID namespace.
_INSTANCE_KEY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")


class AgentInstanceError(ValueError):
    """Base error for the bounded G6.2 logical agent-instance contract."""


class AgentInstanceKeyError(AgentInstanceError):
    """Raised when the caller supplies an invalid logical isolation key."""


class AgentInstanceRoleError(AgentInstanceError):
    """Raised when an instance is requested for an unavailable role."""


def _clean_instance_key(raw: str) -> str:
    if not isinstance(raw, str):
        raise AgentInstanceKeyError("instance_key must be a string")
    if len(raw) > MAX_AGENT_INSTANCE_KEY_CHARS:
        raise AgentInstanceKeyError(
            "instance_key exceeds MAX_AGENT_INSTANCE_KEY_CHARS"
        )
    if _INSTANCE_KEY_RE.fullmatch(raw) is None:
        raise AgentInstanceKeyError(
            "instance_key must match [A-Za-z0-9][A-Za-z0-9_.:-]{0,127}"
        )
    return raw


@dataclass(frozen=True)
class AgentInstance:
    """One immutable logical engineering-agent identity.

    G6.2 deliberately models identity only. It does not bind a TaskRecord,
    context payload, provider/model, prompt, memory, tool set, execution state,
    result, artifact, or approval authority.

    `instance_key` is caller-supplied and bounded. G6.3 may later use a
    canonical TASK ID as this key after deterministic Task -> Agent mapping.
    """

    instance_key: str
    role: RoleDefinition

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "instance_key",
            _clean_instance_key(self.instance_key),
        )
        if not isinstance(self.role, RoleDefinition):
            raise AgentInstanceRoleError(
                "role must be a RoleDefinition"
            )

    @property
    def role_id(self) -> str:
        return self.role.role_id

    @property
    def logical_identity(self) -> str:
        return f"{self.role_id}:{self.instance_key}"


class AgentInstanceFactory:
    """Create role-bound logical agents without spawning a model process.

    The factory owns no mutable project/task state. Known-role validation is
    delegated to the injected G6.1 RoleRegistry so specialized roles remain
    unavailable until explicitly registered by a later approved package.
    """

    def __init__(
        self,
        role_registry: RoleRegistry = DEFAULT_ROLE_REGISTRY,
    ) -> None:
        if not isinstance(role_registry, RoleRegistry):
            raise TypeError("role_registry must be RoleRegistry")
        self._role_registry = role_registry

    @property
    def role_registry(self) -> RoleRegistry:
        return self._role_registry

    def create(
        self,
        *,
        instance_key: str,
        role_id: str,
    ) -> AgentInstance:
        key = _clean_instance_key(instance_key)
        try:
            role = self._role_registry.get(role_id)
        except (RoleRegistryError, UnknownRoleError) as exc:
            raise AgentInstanceRoleError(
                f"cannot create agent instance for role: {role_id!r}"
            ) from exc
        return AgentInstance(
            instance_key=key,
            role=role,
        )


DEFAULT_AGENT_INSTANCE_FACTORY = AgentInstanceFactory()


def create_agent_instance(
    *,
    instance_key: str,
    role_id: str,
) -> AgentInstance:
    """Create one logical instance using the frozen MVP role registry."""

    return DEFAULT_AGENT_INSTANCE_FACTORY.create(
        instance_key=instance_key,
        role_id=role_id,
    )
