from __future__ import annotations

from dataclasses import dataclass
import re
from types import MappingProxyType
from typing import Iterable, Mapping


ROLE_REGISTRY_CONTRACT_VERSION = "1.0"
MAX_ROLE_ID_CHARS = 64
MAX_ROLE_TITLE_CHARS = 96
MAX_ROLE_PURPOSE_BYTES = 2048
MAX_REGISTERED_ROLES = 16

_ROLE_ID_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")

# Frozen baseline section 8 says the initial MVP should use only these roles.
MVP_ROLE_IDS = (
    "coordinator",
    "requirements_engineer",
    "uav_systems_engineer",
    "reviewer",
)

# Specialized roles listed by the frozen architecture are deliberately not active
# in G6.1. They may be added only after the core workflow is stable.
RESERVED_SPECIALIZED_ROLE_IDS = (
    "propulsion_power_engineer",
    "airframe_engineer",
    "px4_flight_engineer",
    "software_engineer",
    "simulation_engineer",
    "verification_engineer",
)


class RoleRegistryError(ValueError):
    """Base error for the deterministic G6.1 role registry."""


class UnknownRoleError(RoleRegistryError):
    """Raised when a role is not registered for the current MVP."""


def _clean_role_id(raw: str) -> str:
    if not isinstance(raw, str) or _ROLE_ID_RE.fullmatch(raw) is None:
        raise RoleRegistryError(
            "role_id must match [a-z][a-z0-9_]{0,63}"
        )
    return raw


def _clean_title(raw: str) -> str:
    if not isinstance(raw, str):
        raise RoleRegistryError("title must be a string")
    if not raw or raw != raw.strip() or len(raw) > MAX_ROLE_TITLE_CHARS:
        raise RoleRegistryError("title must be clean bounded text")
    return raw


def _clean_purpose(raw: str) -> str:
    if not isinstance(raw, str):
        raise RoleRegistryError("purpose must be a string")
    if not raw or raw != raw.strip() or "\x00" in raw:
        raise RoleRegistryError("purpose must be clean non-empty text")
    if len(raw.encode("utf-8")) > MAX_ROLE_PURPOSE_BYTES:
        raise RoleRegistryError("purpose exceeds MAX_ROLE_PURPOSE_BYTES")
    return raw


@dataclass(frozen=True)
class RoleDefinition:
    """Bounded, prompt-free metadata for one logical engineering role.

    G6.1 intentionally does not include provider/model selection, prompt text,
    context, memory, tools, task assignment, or execution state. Those belong
    to later G6 packages.
    """

    role_id: str
    title: str
    purpose: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "role_id", _clean_role_id(self.role_id))
        object.__setattr__(self, "title", _clean_title(self.title))
        object.__setattr__(self, "purpose", _clean_purpose(self.purpose))


_DEFAULT_MVP_ROLES = (
    RoleDefinition(
        role_id="coordinator",
        title="Coordinator",
        purpose=(
            "Coordinates bounded engineering work and project handoffs without "
            "approving its own outputs or bypassing deterministic gates."
        ),
    ),
    RoleDefinition(
        role_id="requirements_engineer",
        title="Requirements Engineer",
        purpose=(
            "Analyzes user requirements, identifies missing information, and "
            "produces requirement-focused proposals for later validation."
        ),
    ),
    RoleDefinition(
        role_id="uav_systems_engineer",
        title="UAV Systems Engineer",
        purpose=(
            "Produces system-level UAV engineering proposals while preserving "
            "facts, assumptions, dependencies, and deterministic validation boundaries."
        ),
    ),
    RoleDefinition(
        role_id="reviewer",
        title="Reviewer",
        purpose=(
            "Provides advisory semantic critique and structured issues; it never "
            "replaces deterministic engineering or UAV verification authority."
        ),
    ),
)


class RoleRegistry:
    """Immutable deterministic registry of known logical agent roles."""

    def __init__(self, roles: Iterable[RoleDefinition]) -> None:
        materialized = tuple(roles)
        if not materialized:
            raise RoleRegistryError("role registry must not be empty")
        if len(materialized) > MAX_REGISTERED_ROLES:
            raise RoleRegistryError("role registry exceeds MAX_REGISTERED_ROLES")

        by_id: dict[str, RoleDefinition] = {}
        for role in materialized:
            if not isinstance(role, RoleDefinition):
                raise RoleRegistryError(
                    "registry entries must be RoleDefinition instances"
                )
            if role.role_id in by_id:
                raise RoleRegistryError(
                    f"duplicate role_id: {role.role_id}"
                )
            by_id[role.role_id] = role

        self._roles = tuple(
            by_id[key]
            for key in sorted(by_id)
        )
        self._by_id: Mapping[str, RoleDefinition] = MappingProxyType(by_id)

    @classmethod
    def default_mvp(cls) -> "RoleRegistry":
        return cls(_DEFAULT_MVP_ROLES)

    def role_ids(self) -> tuple[str, ...]:
        return tuple(role.role_id for role in self._roles)

    def definitions(self) -> tuple[RoleDefinition, ...]:
        return self._roles

    def contains(self, role_id: str) -> bool:
        try:
            canonical = _clean_role_id(role_id)
        except RoleRegistryError:
            return False
        return canonical in self._by_id

    def get(self, role_id: str) -> RoleDefinition:
        canonical = _clean_role_id(role_id)
        try:
            return self._by_id[canonical]
        except KeyError as exc:
            raise UnknownRoleError(
                f"role is not registered for the Phase-6 MVP: {canonical}"
            ) from exc

    def require_known(self, role_id: str) -> str:
        return self.get(role_id).role_id


DEFAULT_ROLE_REGISTRY = RoleRegistry.default_mvp()


def get_default_role_registry() -> RoleRegistry:
    """Return the immutable default MVP role registry."""
    return DEFAULT_ROLE_REGISTRY
