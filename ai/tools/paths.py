from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import os
from pathlib import Path
from types import MappingProxyType
from typing import Iterable, Mapping

from ai.project.store import ProjectNotFoundError, ProjectStore, ProjectStoreConflictError
from ai.tools.allowlists import (
    DEFAULT_ROLE_TOOL_ALLOWLIST_REGISTRY,
    RoleToolAllowlistRegistry,
)
from ai.tools.registry import DEFAULT_TOOL_REGISTRY, ToolRegistry, UnknownToolError


TOOL_PATH_RESTRICTION_CONTRACT_VERSION = "1.0"
MAX_TOOL_RELATIVE_PATH_BYTES = 4096


class ToolPathRestrictionError(ValueError):
    """Base error for deterministic X10.4 path restriction policy."""


class ToolPathDeniedError(ToolPathRestrictionError):
    """Raised when a path would escape or bypass an allowed root."""


class ToolPathNotApplicableError(ToolPathRestrictionError):
    """Raised when a logical tool has no filesystem path interface."""


class ToolPathScope(str, Enum):
    PROJECT = "PROJECT"
    WORKSPACE = "WORKSPACE"


class ToolPathAccess(str, Enum):
    READ = "READ"
    WRITE = "WRITE"


@dataclass(frozen=True)
class ToolPathPolicy:
    """Immutable path requirement for one already-registered logical tool.

    X10.4 describes only root scope and read/write intent. It does not expose a
    file operation, shell/argv command, network URL, structured tool result,
    audit sink, provider binding, or UAV/PX4 handle.
    """

    tool_id: str
    scope: ToolPathScope
    access: ToolPathAccess
    require_exists: bool

    def __post_init__(self) -> None:
        if not isinstance(self.tool_id, str) or not self.tool_id:
            raise ToolPathRestrictionError("tool_id must be a non-empty string")
        if not isinstance(self.scope, ToolPathScope):
            raise ToolPathRestrictionError("scope must be ToolPathScope")
        if not isinstance(self.access, ToolPathAccess):
            raise ToolPathRestrictionError("access must be ToolPathAccess")
        if not isinstance(self.require_exists, bool):
            raise ToolPathRestrictionError("require_exists must be boolean")
        if self.access is ToolPathAccess.READ and not self.require_exists:
            raise ToolPathRestrictionError("read path policies must require an existing target")


_DEFAULT_TOOL_PATH_POLICIES = (
    ToolPathPolicy("artifact_read", ToolPathScope.PROJECT, ToolPathAccess.READ, True),
    ToolPathPolicy("allowlisted_test_runner", ToolPathScope.WORKSPACE, ToolPathAccess.READ, True),
    ToolPathPolicy("draft_write", ToolPathScope.PROJECT, ToolPathAccess.WRITE, False),
    ToolPathPolicy("project_read", ToolPathScope.PROJECT, ToolPathAccess.READ, True),
    ToolPathPolicy("workspace_read", ToolPathScope.WORKSPACE, ToolPathAccess.READ, True),
    ToolPathPolicy("workspace_write", ToolPathScope.WORKSPACE, ToolPathAccess.WRITE, False),
)


class ToolPathPolicyRegistry:
    """Immutable registry of path-bearing logical tools.

    Known tools omitted from this registry are intentionally pathless. In the
    default X10.1 registry those are `calculator` and `issue_create`.
    """

    def __init__(
        self,
        policies: Iterable[ToolPathPolicy],
        *,
        tool_registry: ToolRegistry = DEFAULT_TOOL_REGISTRY,
    ) -> None:
        materialized = tuple(policies)
        if not materialized:
            raise ToolPathRestrictionError("path policy registry must not be empty")

        by_tool: dict[str, ToolPathPolicy] = {}
        for policy in materialized:
            if not isinstance(policy, ToolPathPolicy):
                raise ToolPathRestrictionError("path policies must be ToolPathPolicy instances")
            try:
                canonical_tool = tool_registry.require_known(policy.tool_id)
            except UnknownToolError as exc:
                raise ToolPathRestrictionError(
                    f"path policy tool is not registered: {policy.tool_id}"
                ) from exc
            if canonical_tool in by_tool:
                raise ToolPathRestrictionError(
                    f"duplicate tool_id in path policies: {canonical_tool}"
                )
            by_tool[canonical_tool] = policy

        self._tool_registry = tool_registry
        self._policies = tuple(by_tool[key] for key in sorted(by_tool))
        self._by_tool: Mapping[str, ToolPathPolicy] = MappingProxyType(by_tool)

    @classmethod
    def default(cls) -> "ToolPathPolicyRegistry":
        return cls(_DEFAULT_TOOL_PATH_POLICIES)

    def tool_ids(self) -> tuple[str, ...]:
        return tuple(item.tool_id for item in self._policies)

    def policies(self) -> tuple[ToolPathPolicy, ...]:
        return self._policies

    def get(self, tool_id: str) -> ToolPathPolicy:
        try:
            canonical = self._tool_registry.require_known(tool_id)
        except UnknownToolError as exc:
            raise ToolPathRestrictionError(f"tool is not registered: {tool_id}") from exc
        try:
            return self._by_tool[canonical]
        except KeyError as exc:
            raise ToolPathNotApplicableError(
                f"tool has no filesystem path interface: {canonical}"
            ) from exc


DEFAULT_TOOL_PATH_POLICY_REGISTRY = ToolPathPolicyRegistry.default()


def _canonical_existing_root(root: Path | str, *, label: str) -> Path:
    path = Path(root)
    if not path.is_absolute():
        raise ToolPathDeniedError(f"{label} must be an absolute trusted root")
    if path.is_symlink():
        raise ToolPathDeniedError(f"{label} must not be a symbolic link")
    if not path.exists():
        raise ToolPathDeniedError(f"{label} does not exist")
    if not path.is_dir():
        raise ToolPathDeniedError(f"{label} must be a directory")
    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise ToolPathDeniedError(f"{label} could not be canonicalized") from exc
    # Reject trusted roots whose lexical path itself traverses a symlink. That
    # keeps later relative decisions anchored to one stable filesystem root.
    if resolved != path:
        raise ToolPathDeniedError(f"{label} must be canonical and symlink-free")
    return resolved


def _clean_relative_path(raw: str | os.PathLike[str]) -> tuple[Path, str]:
    try:
        text = os.fspath(raw)
    except TypeError as exc:
        raise ToolPathRestrictionError("relative_path must be path-like") from exc
    if not isinstance(text, str):
        raise ToolPathRestrictionError("relative_path must resolve to text")
    if not text or text == "." or "\x00" in text:
        raise ToolPathDeniedError("relative_path must be non-empty clean text")
    if len(text.encode("utf-8")) > MAX_TOOL_RELATIVE_PATH_BYTES:
        raise ToolPathDeniedError("relative_path exceeds MAX_TOOL_RELATIVE_PATH_BYTES")

    path = Path(text)
    if path.is_absolute():
        raise ToolPathDeniedError("absolute paths are not allowed")
    if not path.parts:
        raise ToolPathDeniedError("relative_path must identify a child path")
    if any(part == ".." for part in path.parts):
        raise ToolPathDeniedError("parent traversal is not allowed")

    normalized = Path(*path.parts)
    if normalized == Path("."):
        raise ToolPathDeniedError("relative_path must identify a child path")
    return normalized, normalized.as_posix()


def _ensure_within(root: Path, candidate: Path) -> None:
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ToolPathDeniedError("resolved path escapes the allowed root") from exc


def _resolve_under_root(
    root: Path | str,
    relative_path: str | os.PathLike[str],
    *,
    require_exists: bool,
    label: str,
) -> tuple[Path, str, bool]:
    canonical_root = _canonical_existing_root(root, label=label)
    relative, relative_text = _clean_relative_path(relative_path)

    current = canonical_root
    parts = relative.parts
    for index, part in enumerate(parts):
        current = current / part
        if current.is_symlink():
            raise ToolPathDeniedError("symbolic links are not allowed in tool paths")
        if current.exists():
            if index < len(parts) - 1 and not current.is_dir():
                raise ToolPathDeniedError("existing path component is not a directory")
            continue

        # A read target must exist. For writes, X10.4 permits a missing final
        # file only; it deliberately does not create arbitrary parent trees.
        if require_exists:
            raise ToolPathDeniedError("requested path does not exist")
        if index < len(parts) - 1:
            raise ToolPathDeniedError("write parent directory does not exist")

    candidate = canonical_root / relative
    try:
        resolved_candidate = candidate.resolve(strict=False)
    except OSError as exc:
        raise ToolPathDeniedError("requested path could not be canonicalized") from exc
    _ensure_within(canonical_root, resolved_candidate)

    if candidate.is_symlink():
        raise ToolPathDeniedError("symbolic link targets are not allowed")
    exists = candidate.exists()
    if require_exists and not exists:
        raise ToolPathDeniedError("requested path does not exist")
    if not require_exists and not exists:
        parent = candidate.parent
        if parent.is_symlink() or not parent.exists() or not parent.is_dir():
            raise ToolPathDeniedError("write parent directory is not a safe directory")

    return resolved_candidate, relative_text, exists


@dataclass(frozen=True)
class ResolvedToolPath:
    """Immutable X10.4 authorization/path-resolution decision.

    This is policy metadata, not the structured tool execution result reserved
    for X10.5.
    """

    role_id: str
    tool_id: str
    scope: ToolPathScope
    access: ToolPathAccess
    root: Path
    relative_path: str
    path: Path
    exists: bool

    def __post_init__(self) -> None:
        if not isinstance(self.scope, ToolPathScope) or not isinstance(self.access, ToolPathAccess):
            raise ToolPathRestrictionError("resolved path scope/access is invalid")
        if not isinstance(self.root, Path) or not self.root.is_absolute():
            raise ToolPathRestrictionError("resolved path root must be absolute")
        if not isinstance(self.path, Path) or not self.path.is_absolute():
            raise ToolPathRestrictionError("resolved tool path must be absolute")
        if not isinstance(self.exists, bool):
            raise ToolPathRestrictionError("exists must be boolean")


def validate_workspace_root(
    *,
    project_dir: Path | str,
    workspace_root: Path | str,
) -> Path:
    """Validate a trusted pre-existing isolated workspace root.

    X10.4 never creates a workspace. The future Software Agent workspace must be
    a strict descendant of `<project>/software`; using the entire software tree
    as a workspace is denied.
    """

    project = _canonical_existing_root(project_dir, label="project root")
    software = _canonical_existing_root(project / "software", label="project software root")
    workspace = _canonical_existing_root(workspace_root, label="workspace root")
    if workspace == software:
        raise ToolPathDeniedError("workspace root must be isolated below the software root")
    _ensure_within(software, workspace)
    return workspace


class ProjectToolPathGuard:
    """Bind X10.2 authorization to bounded project/workspace path resolution."""

    def __init__(
        self,
        project_store: ProjectStore,
        *,
        allowlists: RoleToolAllowlistRegistry = DEFAULT_ROLE_TOOL_ALLOWLIST_REGISTRY,
        policies: ToolPathPolicyRegistry = DEFAULT_TOOL_PATH_POLICY_REGISTRY,
    ) -> None:
        if not isinstance(project_store, ProjectStore):
            raise TypeError("project_store must be ProjectStore")
        if not isinstance(allowlists, RoleToolAllowlistRegistry):
            raise TypeError("allowlists must be RoleToolAllowlistRegistry")
        if not isinstance(policies, ToolPathPolicyRegistry):
            raise TypeError("policies must be ToolPathPolicyRegistry")
        self._projects = project_store
        self._allowlists = allowlists
        self._policies = policies

    def resolve(
        self,
        *,
        project_id: object,
        role_id: str,
        tool_id: str,
        relative_path: str | os.PathLike[str],
        workspace_root: Path | str | None = None,
    ) -> ResolvedToolPath:
        canonical_tool = self._allowlists.require_allowed(role_id, tool_id)
        canonical_role = self._allowlists.get(role_id).role_id
        policy = self._policies.get(canonical_tool)

        try:
            handle = self._projects.open_project(project_id)  # type: ignore[arg-type]
        except (ProjectNotFoundError, ProjectStoreConflictError) as exc:
            raise ToolPathRestrictionError(f"project could not be opened: {exc}") from exc

        if policy.scope is ToolPathScope.PROJECT:
            if workspace_root is not None:
                raise ToolPathDeniedError("workspace_root is not valid for a project-scoped tool")
            root = _canonical_existing_root(handle.layout.project_dir, label="project root")
        else:
            if workspace_root is None:
                raise ToolPathDeniedError("workspace-scoped tool requires a trusted workspace_root")
            root = validate_workspace_root(
                project_dir=handle.layout.project_dir,
                workspace_root=workspace_root,
            )

        path, relative_text, exists = _resolve_under_root(
            root,
            relative_path,
            require_exists=policy.require_exists,
            label=f"{policy.scope.value.lower()} root",
        )
        return ResolvedToolPath(
            role_id=canonical_role,
            tool_id=canonical_tool,
            scope=policy.scope,
            access=policy.access,
            root=root,
            relative_path=relative_text,
            path=path,
            exists=exists,
        )


def get_default_tool_path_policy_registry() -> ToolPathPolicyRegistry:
    return DEFAULT_TOOL_PATH_POLICY_REGISTRY
