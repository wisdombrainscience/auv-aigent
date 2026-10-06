from __future__ import annotations

import ast
from pathlib import Path
import time

import pytest

from ai.project.store import ProjectStore
from ai.tools import (
    TOOL_AUDIT_CONTRACT_VERSION,
    TOOL_AUDIT_FILENAME,
    ToolAuditError,
    ToolResultStatus,
    read_tool_audit,
    run_tool_audited,
)


def _project(tmp_path: Path):
    return ProjectStore(projects_root=tmp_path).create_project()


def _ok():
    return {"answer": 42}


def _slow():
    time.sleep(0.3)
    return "late"


def test_contract_version_is_frozen():
    assert TOOL_AUDIT_CONTRACT_VERSION == "1.0"


def test_success_is_logged_and_survives_reopen(tmp_path: Path):
    handle = _project(tmp_path)
    result = run_tool_audited(handle=handle, role_id="uav_systems_engineer", tool_id="calculator", operation=_ok, timeout_s=2)
    assert result.status is ToolResultStatus.SUCCESS
    records = read_tool_audit(handle)
    assert len(records) == 1
    assert records[0].sequence == 1
    assert records[0].project_id == handle.project_id.value
    assert records[0].result == result
    reopened = ProjectStore(projects_root=tmp_path).open_project(handle.project_id)
    assert read_tool_audit(reopened)[0].result == result


def test_denied_attempt_is_logged(tmp_path: Path):
    handle = _project(tmp_path)
    result = run_tool_audited(handle=handle, role_id="reviewer", tool_id="calculator", operation=_ok, timeout_s=2)
    assert result.failure is not None and result.failure.code == "TOOL_NOT_ALLOWED"
    assert read_tool_audit(handle)[0].result.failure.code == "TOOL_NOT_ALLOWED"


def test_timeout_is_logged(tmp_path: Path):
    handle = _project(tmp_path)
    result = run_tool_audited(handle=handle, role_id="requirements_engineer", tool_id="project_read", operation=_slow, timeout_s=0.03)
    assert result.failure is not None and result.failure.code == "TOOL_TIMEOUT"
    assert read_tool_audit(handle)[0].result.failure.code == "TOOL_TIMEOUT"


def test_sequence_is_contiguous(tmp_path: Path):
    handle = _project(tmp_path)
    for _ in range(3):
        run_tool_audited(handle=handle, role_id="uav_systems_engineer", tool_id="calculator", operation=_ok, timeout_s=2)
    assert [r.sequence for r in read_tool_audit(handle)] == [1, 2, 3]


def test_log_is_project_local(tmp_path: Path):
    handle = _project(tmp_path)
    run_tool_audited(handle=handle, role_id="uav_systems_engineer", tool_id="calculator", operation=_ok, timeout_s=2)
    expected = handle.layout.data_directories["ai_runs"] / TOOL_AUDIT_FILENAME
    assert expected.is_file()
    assert expected.stat().st_mode & 0o077 == 0


def test_symlink_log_is_rejected(tmp_path: Path):
    handle = _project(tmp_path)
    outside = tmp_path / "outside"
    outside.write_text("", encoding="utf-8")
    log = handle.layout.data_directories["ai_runs"] / TOOL_AUDIT_FILENAME
    log.symlink_to(outside)
    with pytest.raises(ToolAuditError, match="symbolic link"):
        read_tool_audit(handle)


def test_partial_or_corrupt_log_fails_closed(tmp_path: Path):
    handle = _project(tmp_path)
    log = handle.layout.data_directories["ai_runs"] / TOOL_AUDIT_FILENAME
    log.write_bytes(b'{"bad":true}')
    with pytest.raises(ToolAuditError, match="partial"):
        read_tool_audit(handle)


def test_tampered_sequence_fails_closed(tmp_path: Path):
    handle = _project(tmp_path)
    run_tool_audited(handle=handle, role_id="uav_systems_engineer", tool_id="calculator", operation=_ok, timeout_s=2)
    log = handle.layout.data_directories["ai_runs"] / TOOL_AUDIT_FILENAME
    text = log.read_text(encoding="utf-8").replace('"sequence":1', '"sequence":2')
    log.write_text(text, encoding="utf-8")
    with pytest.raises(ToolAuditError, match="sequence"):
        read_tool_audit(handle)


def test_x106_does_not_change_x105_result_fields(tmp_path: Path):
    handle = _project(tmp_path)
    result = run_tool_audited(handle=handle, role_id="uav_systems_engineer", tool_id="calculator", operation=_ok, timeout_s=2)
    assert set(result.to_dict()) == {"status", "role_id", "tool_id", "output", "failure"}


def test_x106_module_has_no_shell_network_or_px4_imports():
    source_path = Path(__file__).resolve().parents[2] / "ai" / "tools" / "audit.py"
    tree = ast.parse(source_path.read_text(encoding="utf-8"))
    forbidden = {"subprocess", "socket", "requests", "mavsdk", "px4", "pymavlink"}
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0].lower() for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0].lower())
    assert imported.isdisjoint(forbidden)
