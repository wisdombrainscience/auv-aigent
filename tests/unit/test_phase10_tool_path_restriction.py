from __future__ import annotations

from dataclasses import fields
from pathlib import Path
import ast
import inspect
import subprocess
import sys

import pytest

from ai.project import ProjectStore
from ai.tools import (
    DEFAULT_ROLE_TOOL_ALLOWLIST_REGISTRY,
    DEFAULT_TOOL_PATH_POLICY_REGISTRY,
    DEFAULT_TOOL_REGISTRY,
    MAX_TOOL_RELATIVE_PATH_BYTES,
    TOOL_PATH_RESTRICTION_CONTRACT_VERSION,
    ProjectToolPathGuard,
    ResolvedToolPath,
    ToolNotAllowedError,
    ToolPathAccess,
    ToolPathDeniedError,
    ToolPathNotApplicableError,
    ToolPathPolicy,
    ToolPathPolicyRegistry,
    ToolPathRestrictionError,
    ToolPathScope,
    validate_workspace_root,
)


def _project(tmp_path: Path):
    store = ProjectStore(tmp_path / "state" / "projects")
    handle = store.create_project()
    return store, handle


def test_contract_version_is_frozen() -> None:
    assert TOOL_PATH_RESTRICTION_CONTRACT_VERSION == "1.0"


def test_relative_path_limit_is_bounded() -> None:
    assert MAX_TOOL_RELATIVE_PATH_BYTES == 4096


def test_policy_wire_fields_are_minimal() -> None:
    assert tuple(field.name for field in fields(ToolPathPolicy)) == (
        "tool_id", "scope", "access", "require_exists"
    )


def test_resolved_decision_fields_are_bounded_policy_metadata() -> None:
    assert tuple(field.name for field in fields(ResolvedToolPath)) == (
        "role_id", "tool_id", "scope", "access", "root", "relative_path", "path", "exists"
    )


def test_default_path_policy_tool_set_is_exact() -> None:
    assert DEFAULT_TOOL_PATH_POLICY_REGISTRY.tool_ids() == (
        "allowlisted_test_runner",
        "artifact_read",
        "draft_write",
        "project_read",
        "workspace_read",
        "workspace_write",
    )


def test_pathless_known_tools_are_explicitly_not_applicable() -> None:
    for tool_id in ("calculator", "issue_create"):
        with pytest.raises(ToolPathNotApplicableError):
            DEFAULT_TOOL_PATH_POLICY_REGISTRY.get(tool_id)


def test_unknown_tool_fails_closed_in_path_policy_registry() -> None:
    with pytest.raises(ToolPathRestrictionError, match="not registered"):
        DEFAULT_TOOL_PATH_POLICY_REGISTRY.get("future_tool")


def test_project_read_resolves_existing_relative_path(tmp_path: Path) -> None:
    store, handle = _project(tmp_path)
    target = handle.layout.data_directories["requirements"] / "note.txt"
    target.write_text("ok")
    resolved = ProjectToolPathGuard(store).resolve(
        project_id=handle.project_id,
        role_id="requirements_engineer",
        tool_id="project_read",
        relative_path="requirements/note.txt",
    )
    assert resolved.path == target
    assert resolved.root == handle.layout.project_dir
    assert resolved.relative_path == "requirements/note.txt"
    assert resolved.scope is ToolPathScope.PROJECT
    assert resolved.access is ToolPathAccess.READ
    assert resolved.exists is True


def test_draft_write_allows_missing_final_file_with_existing_parent(tmp_path: Path) -> None:
    store, handle = _project(tmp_path)
    target = handle.layout.data_directories["architecture"] / "candidate.json"
    resolved = ProjectToolPathGuard(store).resolve(
        project_id=handle.project_id,
        role_id="requirements_engineer",
        tool_id="draft_write",
        relative_path="architecture/candidate.json",
    )
    assert resolved.path == target
    assert resolved.access is ToolPathAccess.WRITE
    assert resolved.exists is False
    assert not target.exists()


def test_draft_write_does_not_create_missing_parent_tree(tmp_path: Path) -> None:
    store, handle = _project(tmp_path)
    with pytest.raises(ToolPathDeniedError, match="parent directory"):
        ProjectToolPathGuard(store).resolve(
            project_id=handle.project_id,
            role_id="requirements_engineer",
            tool_id="draft_write",
            relative_path="architecture/new/subdir/candidate.json",
        )
    assert not (handle.layout.project_dir / "architecture/new").exists()


def test_read_missing_target_is_denied(tmp_path: Path) -> None:
    store, handle = _project(tmp_path)
    with pytest.raises(ToolPathDeniedError, match="does not exist"):
        ProjectToolPathGuard(store).resolve(
            project_id=handle.project_id,
            role_id="requirements_engineer",
            tool_id="project_read",
            relative_path="requirements/missing.txt",
        )


@pytest.mark.parametrize("raw", ["../outside", "requirements/../../outside", "requirements/../note.txt"])
def test_parent_traversal_is_denied_even_if_it_could_normalize_inside(tmp_path: Path, raw: str) -> None:
    store, handle = _project(tmp_path)
    with pytest.raises(ToolPathDeniedError, match="parent traversal"):
        ProjectToolPathGuard(store).resolve(
            project_id=handle.project_id,
            role_id="requirements_engineer",
            tool_id="project_read",
            relative_path=raw,
        )


def test_absolute_path_is_denied_even_when_inside_project(tmp_path: Path) -> None:
    store, handle = _project(tmp_path)
    target = handle.layout.project_json
    with pytest.raises(ToolPathDeniedError, match="absolute"):
        ProjectToolPathGuard(store).resolve(
            project_id=handle.project_id,
            role_id="requirements_engineer",
            tool_id="project_read",
            relative_path=str(target),
        )


@pytest.mark.parametrize("raw", ["", ".", "bad\x00name"])
def test_empty_dot_or_nul_path_is_denied(tmp_path: Path, raw: str) -> None:
    store, handle = _project(tmp_path)
    with pytest.raises(ToolPathDeniedError):
        ProjectToolPathGuard(store).resolve(
            project_id=handle.project_id,
            role_id="requirements_engineer",
            tool_id="project_read",
            relative_path=raw,
        )


def test_oversized_relative_path_is_denied_before_filesystem_use(tmp_path: Path) -> None:
    store, handle = _project(tmp_path)
    raw = "a" * (MAX_TOOL_RELATIVE_PATH_BYTES + 1)
    with pytest.raises(ToolPathDeniedError, match="MAX_TOOL_RELATIVE_PATH_BYTES"):
        ProjectToolPathGuard(store).resolve(
            project_id=handle.project_id,
            role_id="requirements_engineer",
            tool_id="project_read",
            relative_path=raw,
        )


def test_final_symlink_escape_is_denied(tmp_path: Path) -> None:
    store, handle = _project(tmp_path)
    outside = tmp_path / "outside.txt"
    outside.write_text("secret")
    link = handle.layout.data_directories["requirements"] / "escape"
    link.symlink_to(outside)
    with pytest.raises(ToolPathDeniedError, match="symbolic link"):
        ProjectToolPathGuard(store).resolve(
            project_id=handle.project_id,
            role_id="requirements_engineer",
            tool_id="project_read",
            relative_path="requirements/escape",
        )


def test_directory_symlink_escape_is_denied(tmp_path: Path) -> None:
    store, handle = _project(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("secret")
    link = handle.layout.data_directories["requirements"] / "escape-dir"
    link.symlink_to(outside, target_is_directory=True)
    with pytest.raises(ToolPathDeniedError, match="symbolic link"):
        ProjectToolPathGuard(store).resolve(
            project_id=handle.project_id,
            role_id="requirements_engineer",
            tool_id="project_read",
            relative_path="requirements/escape-dir/secret.txt",
        )


def test_broken_symlink_is_denied(tmp_path: Path) -> None:
    store, handle = _project(tmp_path)
    link = handle.layout.data_directories["requirements"] / "broken"
    link.symlink_to(tmp_path / "does-not-exist")
    with pytest.raises(ToolPathDeniedError, match="symbolic link"):
        ProjectToolPathGuard(store).resolve(
            project_id=handle.project_id,
            role_id="requirements_engineer",
            tool_id="project_read",
            relative_path="requirements/broken",
        )


def test_symlink_to_inside_project_is_still_denied(tmp_path: Path) -> None:
    store, handle = _project(tmp_path)
    real = handle.layout.data_directories["requirements"] / "real.txt"
    real.write_text("ok")
    link = handle.layout.data_directories["requirements"] / "alias.txt"
    link.symlink_to(real)
    with pytest.raises(ToolPathDeniedError, match="symbolic link"):
        ProjectToolPathGuard(store).resolve(
            project_id=handle.project_id,
            role_id="requirements_engineer",
            tool_id="project_read",
            relative_path="requirements/alias.txt",
        )


def test_allowlist_is_checked_before_path_resolution(tmp_path: Path) -> None:
    store, handle = _project(tmp_path)
    outside = tmp_path / "outside.txt"
    outside.write_text("x")
    with pytest.raises(ToolNotAllowedError):
        ProjectToolPathGuard(store).resolve(
            project_id=handle.project_id,
            role_id="reviewer",
            tool_id="draft_write",
            relative_path=str(outside),
        )


def test_coordinator_empty_allowlist_remains_enforced(tmp_path: Path) -> None:
    store, handle = _project(tmp_path)
    with pytest.raises(ToolNotAllowedError):
        ProjectToolPathGuard(store).resolve(
            project_id=handle.project_id,
            role_id="coordinator",
            tool_id="project_read",
            relative_path="project.json",
        )


def test_pathless_allowed_calculator_cannot_receive_a_filesystem_path(tmp_path: Path) -> None:
    store, handle = _project(tmp_path)
    with pytest.raises(ToolPathNotApplicableError, match="no filesystem path"):
        ProjectToolPathGuard(store).resolve(
            project_id=handle.project_id,
            role_id="uav_systems_engineer",
            tool_id="calculator",
            relative_path="project.json",
        )


def test_workspace_root_must_be_strict_descendant_of_project_software(tmp_path: Path) -> None:
    store, handle = _project(tmp_path)
    software = handle.layout.data_directories["software"]
    workspace = software / "task-workspace"
    workspace.mkdir()
    assert validate_workspace_root(
        project_dir=handle.layout.project_dir,
        workspace_root=workspace,
    ) == workspace
    with pytest.raises(ToolPathDeniedError, match="isolated"):
        validate_workspace_root(
            project_dir=handle.layout.project_dir,
            workspace_root=software,
        )


def test_workspace_root_outside_project_is_denied(tmp_path: Path) -> None:
    store, handle = _project(tmp_path)
    outside = tmp_path / "workspace"
    outside.mkdir()
    with pytest.raises(ToolPathDeniedError, match="escapes"):
        validate_workspace_root(
            project_dir=handle.layout.project_dir,
            workspace_root=outside,
        )


def test_workspace_root_symlink_is_denied(tmp_path: Path) -> None:
    store, handle = _project(tmp_path)
    software = handle.layout.data_directories["software"]
    real = software / "real-workspace"
    real.mkdir()
    link = software / "workspace-link"
    link.symlink_to(real, target_is_directory=True)
    with pytest.raises(ToolPathDeniedError, match="symbolic link"):
        validate_workspace_root(
            project_dir=handle.layout.project_dir,
            workspace_root=link,
        )


def test_x10_4_does_not_create_software_workspace(tmp_path: Path) -> None:
    _, handle = _project(tmp_path)
    software = handle.layout.data_directories["software"]
    assert tuple(software.iterdir()) == ()


def test_default_workspace_tools_remain_ungranted_to_active_roles() -> None:
    for role_id in DEFAULT_ROLE_TOOL_ALLOWLIST_REGISTRY.role_ids():
        granted = set(DEFAULT_ROLE_TOOL_ALLOWLIST_REGISTRY.allowed_tool_ids(role_id))
        assert not granted.intersection({"workspace_read", "workspace_write", "allowlisted_test_runner"})


def test_x10_1_registry_is_unchanged() -> None:
    assert len(DEFAULT_TOOL_REGISTRY.tool_ids()) == 8


def test_path_module_has_no_shell_network_px4_or_mavsdk_imports() -> None:
    import ai.tools.paths as module
    tree = ast.parse(inspect.getsource(module))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    assert "subprocess" not in imported
    assert "socket" not in imported
    assert all(not name.startswith("mavsdk") for name in imported)
    assert all("px4" not in name.lower() for name in imported)


def test_path_guard_exposes_no_file_open_shell_command_or_url_api() -> None:
    params = set(inspect.signature(ProjectToolPathGuard.resolve).parameters)
    assert not params.intersection({"command", "argv", "shell", "url", "network", "px4", "mavsdk"})
    guard_methods = set(dir(ProjectToolPathGuard))
    for name in ("open", "read", "write", "execute", "run_command", "audit"):
        assert name not in guard_methods


def test_x10_4_acceptance_probe_runs_directly_from_project_root() -> None:
    project_root = Path(__file__).resolve().parents[2]
    proc = subprocess.run(
        [sys.executable, str(project_root / "tools/check_phase10_path_restriction.py")],
        cwd=str(project_root),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=30,
        check=False,
    )
    assert proc.returncode == 0, proc.stdout
    assert "TOOL_PATH_RESTRICTION=PASS" in proc.stdout
