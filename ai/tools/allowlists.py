from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Iterable, Mapping

from ai.agents.roles import DEFAULT_ROLE_REGISTRY, RoleRegistry, UnknownRoleError
from ai.tools.registry import DEFAULT_TOOL_REGISTRY, ToolRegistry, UnknownToolError


ROLE_TOOL_ALLOWLIST_CONTRACT_VERSION = "1.0"
MAX_ROLE_TOOL_ALLOWLIST_ENTRIES = 16


class RoleToolAllowlistError(ValueError):
    """Base error for deterministic X10.2 role-specific tool allowlists."""


class ToolNotAllowedError(RoleToolAllowlistError):
    """Raised when a known tool is not allowlisted for a known role."""


@dataclass(frozen=True)
class RoleToolAllowlist:
    """Immutable allowlist metadata for one already-registered role.

    X10.2 contains identifiers only. It has no callable/executor, timeout,
    filesystem path policy, result adapter, audit sink, provider binding, or
    UAV/PX4 handle. Those responsibilities remain reserved for later gates.
    """

    role_id: str
    tool_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.role_id, str) or not self.role_id:
            raise RoleToolAllowlistError("role_id must be a non-empty string")
        if not isinstance(self.tool_ids, tuple):
            raise RoleToolAllowlistError("tool_ids must be a tuple")
        if len(self.tool_ids) > MAX_ROLE_TOOL_ALLOWLIST_ENTRIES:
            raise RoleToolAllowlistError(
                "tool_ids exceeds MAX_ROLE_TOOL_ALLOWLIST_ENTRIES"
            )
        if any(not isinstance(tool_id, str) or not tool_id for tool_id in self.tool_ids):
            raise RoleToolAllowlistError("tool_ids must contain non-empty strings")
        if len(set(self.tool_ids)) != len(self.tool_ids):
            raise RoleToolAllowlistError("duplicate tool_id in role allowlist")
        if tuple(sorted(self.tool_ids)) != self.tool_ids:
            raise RoleToolAllowlistError("tool_ids must be sorted deterministically")


# Frozen architecture section 22 examples mapped only to currently active roles.
# Coordinator has no explicit tool grant in the frozen baseline, so least
# privilege is an empty allowlist. software_engineer remains a reserved role.
_DEFAULT_ROLE_TOOL_ALLOWLISTS = (
    RoleToolAllowlist(role_id="coordinator", tool_ids=()),
    RoleToolAllowlist(
        role_id="requirements_engineer",
        tool_ids=("artifact_read", "draft_write", "project_read"),
    ),
    RoleToolAllowlist(
        role_id="reviewer",
        tool_ids=("artifact_read", "issue_create", "project_read"),
    ),
    RoleToolAllowlist(
        role_id="uav_systems_engineer",
        tool_ids=("artifact_read", "calculator", "draft_write", "project_read"),
    ),
)


class RoleToolAllowlistRegistry:
    """Immutable deny-by-default mapping from frozen roles to known tools."""

    def __init__(
        self,
        allowlists: Iterable[RoleToolAllowlist],
        *,
        role_registry: RoleRegistry = DEFAULT_ROLE_REGISTRY,
        tool_registry: ToolRegistry = DEFAULT_TOOL_REGISTRY,
    ) -> None:
        materialized = tuple(allowlists)
        if not materialized:
            raise RoleToolAllowlistError("role tool allowlist registry must not be empty")

        by_role: dict[str, RoleToolAllowlist] = {}
        for item in materialized:
            if not isinstance(item, RoleToolAllowlist):
                raise RoleToolAllowlistError(
                    "allowlist entries must be RoleToolAllowlist instances"
                )
            try:
                canonical_role = role_registry.require_known(item.role_id)
            except UnknownRoleError as exc:
                raise RoleToolAllowlistError(
                    f"allowlist role is not registered: {item.role_id}"
                ) from exc
            if canonical_role in by_role:
                raise RoleToolAllowlistError(
                    f"duplicate role_id in allowlists: {canonical_role}"
                )

            canonical_tools: list[str] = []
            for tool_id in item.tool_ids:
                try:
                    canonical_tools.append(tool_registry.require_known(tool_id))
                except UnknownToolError as exc:
                    raise RoleToolAllowlistError(
                        f"allowlist tool is not registered: {tool_id}"
                    ) from exc

            canonical_tuple = tuple(sorted(canonical_tools))
            if canonical_tuple != item.tool_ids:
                raise RoleToolAllowlistError(
                    f"allowlist tools are not canonical for role: {canonical_role}"
                )
            by_role[canonical_role] = item

        registered_roles = set(role_registry.role_ids())
        allowlisted_roles = set(by_role)
        if allowlisted_roles != registered_roles:
            missing = sorted(registered_roles - allowlisted_roles)
            extra = sorted(allowlisted_roles - registered_roles)
            raise RoleToolAllowlistError(
                f"allowlists must cover registered roles exactly: missing={missing} extra={extra}"
            )

        self._role_registry = role_registry
        self._tool_registry = tool_registry
        self._allowlists = tuple(by_role[key] for key in sorted(by_role))
        self._by_role: Mapping[str, RoleToolAllowlist] = MappingProxyType(by_role)

    @classmethod
    def default(cls) -> "RoleToolAllowlistRegistry":
        return cls(_DEFAULT_ROLE_TOOL_ALLOWLISTS)

    def role_ids(self) -> tuple[str, ...]:
        return tuple(item.role_id for item in self._allowlists)

    def entries(self) -> tuple[RoleToolAllowlist, ...]:
        return self._allowlists

    def get(self, role_id: str) -> RoleToolAllowlist:
        try:
            canonical_role = self._role_registry.require_known(role_id)
        except UnknownRoleError as exc:
            raise RoleToolAllowlistError(f"role is not registered: {role_id}") from exc
        return self._by_role[canonical_role]

    def allowed_tool_ids(self, role_id: str) -> tuple[str, ...]:
        return self.get(role_id).tool_ids

    def is_allowed(self, role_id: str, tool_id: str) -> bool:
        try:
            canonical_role = self._role_registry.require_known(role_id)
            canonical_tool = self._tool_registry.require_known(tool_id)
        except (UnknownRoleError, UnknownToolError):
            return False
        return canonical_tool in self._by_role[canonical_role].tool_ids

    def require_allowed(self, role_id: str, tool_id: str) -> str:
        try:
            canonical_role = self._role_registry.require_known(role_id)
        except UnknownRoleError as exc:
            raise RoleToolAllowlistError(f"role is not registered: {role_id}") from exc
        try:
            canonical_tool = self._tool_registry.require_known(tool_id)
        except UnknownToolError as exc:
            raise RoleToolAllowlistError(f"tool is not registered: {tool_id}") from exc
        if canonical_tool not in self._by_role[canonical_role].tool_ids:
            raise ToolNotAllowedError(
                f"tool is not allowlisted for role: role={canonical_role} tool={canonical_tool}"
            )
        return canonical_tool


DEFAULT_ROLE_TOOL_ALLOWLIST_REGISTRY = RoleToolAllowlistRegistry.default()


def get_default_role_tool_allowlist_registry() -> RoleToolAllowlistRegistry:
    """Return the immutable default X10.2 role-tool allowlist registry."""
    return DEFAULT_ROLE_TOOL_ALLOWLIST_REGISTRY
