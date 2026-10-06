from __future__ import annotations

from dataclasses import dataclass
import re
from types import MappingProxyType
from typing import Iterable, Mapping


TOOL_REGISTRY_CONTRACT_VERSION = "1.0"
MAX_TOOL_ID_CHARS = 64
MAX_TOOL_TITLE_CHARS = 96
MAX_TOOL_PURPOSE_BYTES = 2048
MAX_REGISTERED_TOOLS = 32

_TOOL_ID_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")

# Frozen architecture section 22 examples. X10.1 registers identifiers only:
# role permissions belong to X10.2 and execution belongs to later gates.
DEFAULT_TOOL_IDS = (
    "project_read",
    "artifact_read",
    "draft_write",
    "calculator",
    "workspace_read",
    "workspace_write",
    "allowlisted_test_runner",
    "issue_create",
)

# Frozen architecture section 22: these capabilities are forbidden by default.
# They are deliberately NOT entries in DEFAULT_TOOL_REGISTRY.
FORBIDDEN_BY_DEFAULT_TOOL_IDS = (
    "unrestricted_shell",
    "arbitrary_filesystem_path",
    "arbitrary_network_access",
    "direct_px4_adapter",
    "direct_raw_mavsdk_command",
)


class ToolRegistryError(ValueError):
    """Base error for the deterministic X10.1 tool registry."""


class UnknownToolError(ToolRegistryError):
    """Raised when a tool identifier is not registered."""


def _clean_tool_id(raw: str) -> str:
    if not isinstance(raw, str) or _TOOL_ID_RE.fullmatch(raw) is None:
        raise ToolRegistryError("tool_id must match [a-z][a-z0-9_]{0,63}")
    return raw


def _clean_title(raw: str) -> str:
    if not isinstance(raw, str):
        raise ToolRegistryError("title must be a string")
    if not raw or raw != raw.strip() or len(raw) > MAX_TOOL_TITLE_CHARS:
        raise ToolRegistryError("title must be clean bounded text")
    return raw


def _clean_purpose(raw: str) -> str:
    if not isinstance(raw, str):
        raise ToolRegistryError("purpose must be a string")
    if not raw or raw != raw.strip() or "\x00" in raw:
        raise ToolRegistryError("purpose must be clean non-empty text")
    if len(raw.encode("utf-8")) > MAX_TOOL_PURPOSE_BYTES:
        raise ToolRegistryError("purpose exceeds MAX_TOOL_PURPOSE_BYTES")
    return raw


@dataclass(frozen=True)
class ToolDefinition:
    """Prompt-free metadata for a logical tool capability.

    X10.1 contains no callable, process command, role permission, timeout,
    filesystem path policy, structured result adapter, audit sink, provider
    binding, or UAV/PX4 handle. Those responsibilities belong to later gates.
    """

    tool_id: str
    title: str
    purpose: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "tool_id", _clean_tool_id(self.tool_id))
        object.__setattr__(self, "title", _clean_title(self.title))
        object.__setattr__(self, "purpose", _clean_purpose(self.purpose))


_DEFAULT_TOOLS = (
    ToolDefinition(
        tool_id="project_read",
        title="Project Read",
        purpose="Reads bounded project-state information exposed by the project layer.",
    ),
    ToolDefinition(
        tool_id="artifact_read",
        title="Artifact Read",
        purpose="Reads immutable artifact versions through the artifact registry boundary.",
    ),
    ToolDefinition(
        tool_id="draft_write",
        title="Draft Write",
        purpose="Writes draft-only outputs without approving or executing them.",
    ),
    ToolDefinition(
        tool_id="calculator",
        title="Calculator",
        purpose="Performs bounded deterministic calculations exposed by approved tooling.",
    ),
    ToolDefinition(
        tool_id="workspace_read",
        title="Workspace Read",
        purpose="Reads files from a future isolated software-task workspace.",
    ),
    ToolDefinition(
        tool_id="workspace_write",
        title="Workspace Write",
        purpose="Writes files only inside a future isolated software-task workspace.",
    ),
    ToolDefinition(
        tool_id="allowlisted_test_runner",
        title="Allowlisted Test Runner",
        purpose="Represents a future allowlisted test-running capability without arbitrary shell access.",
    ),
    ToolDefinition(
        tool_id="issue_create",
        title="Issue Create",
        purpose="Creates structured review issues through a future controlled issue boundary.",
    ),
)


class ToolRegistry:
    """Immutable deterministic registry of known logical tool capabilities."""

    def __init__(self, tools: Iterable[ToolDefinition]) -> None:
        materialized = tuple(tools)
        if not materialized:
            raise ToolRegistryError("tool registry must not be empty")
        if len(materialized) > MAX_REGISTERED_TOOLS:
            raise ToolRegistryError("tool registry exceeds MAX_REGISTERED_TOOLS")

        by_id: dict[str, ToolDefinition] = {}
        for tool in materialized:
            if not isinstance(tool, ToolDefinition):
                raise ToolRegistryError(
                    "registry entries must be ToolDefinition instances"
                )
            if tool.tool_id in by_id:
                raise ToolRegistryError(f"duplicate tool_id: {tool.tool_id}")
            by_id[tool.tool_id] = tool

        self._tools = tuple(by_id[key] for key in sorted(by_id))
        self._by_id: Mapping[str, ToolDefinition] = MappingProxyType(by_id)

    @classmethod
    def default(cls) -> "ToolRegistry":
        return cls(_DEFAULT_TOOLS)

    def tool_ids(self) -> tuple[str, ...]:
        return tuple(tool.tool_id for tool in self._tools)

    def definitions(self) -> tuple[ToolDefinition, ...]:
        return self._tools

    def contains(self, tool_id: str) -> bool:
        try:
            canonical = _clean_tool_id(tool_id)
        except ToolRegistryError:
            return False
        return canonical in self._by_id

    def get(self, tool_id: str) -> ToolDefinition:
        canonical = _clean_tool_id(tool_id)
        try:
            return self._by_id[canonical]
        except KeyError as exc:
            raise UnknownToolError(f"tool is not registered: {canonical}") from exc

    def require_known(self, tool_id: str) -> str:
        return self.get(tool_id).tool_id


DEFAULT_TOOL_REGISTRY = ToolRegistry.default()


def get_default_tool_registry() -> ToolRegistry:
    """Return the immutable default X10.1 tool registry."""
    return DEFAULT_TOOL_REGISTRY
