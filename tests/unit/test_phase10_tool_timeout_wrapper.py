from __future__ import annotations

from dataclasses import fields
from pathlib import Path
import ast
import functools
import inspect
import math
import multiprocessing
import time

import pytest

from ai.contracts.failures import FailureClass
from ai.tasks.lifecycle import TASK_TIMEOUT_S
from ai.tools import (
    DEFAULT_ROLE_TOOL_ALLOWLIST_REGISTRY,
    DEFAULT_TOOL_REGISTRY,
    MAX_TOOL_TIMEOUT_S,
    TOOL_TIMEOUT_CONTRACT_VERSION,
    ToolInvocationError,
    ToolNotAllowedError,
    ToolTimedOutError,
    ToolTimeoutPolicy,
    ToolTimeoutPolicyError,
    ToolTimeoutWrapper,
    RoleToolAllowlistError,
    run_tool_with_timeout,
)


def _return_42() -> int:
    return 42


def _return_ok() -> str:
    return "ok"


def _delayed_write(path_text: str) -> None:
    time.sleep(0.3)
    Path(path_text).write_text("late")


def _raise_value_error() -> None:
    raise ValueError("boom")


def _sleep_long() -> str:
    time.sleep(2.0)
    return "late"


def _spawn_nested_process() -> str:
    proc = multiprocessing.Process(target=time.sleep, args=(0.01,))
    proc.start()
    proc.join()
    return "spawned"


def _unpickleable_result():
    return lambda: None


def test_contract_version_is_frozen() -> None:
    assert TOOL_TIMEOUT_CONTRACT_VERSION == "1.0"


def test_max_timeout_never_exceeds_frozen_task_timeout() -> None:
    assert MAX_TOOL_TIMEOUT_S == 300.0
    assert MAX_TOOL_TIMEOUT_S == TASK_TIMEOUT_S


def test_policy_wire_fields_are_minimal() -> None:
    assert tuple(field.name for field in fields(ToolTimeoutPolicy)) == ("timeout_s",)


@pytest.mark.parametrize("value", [0.001, 0.1, 1, 30.5, 300.0])
def test_policy_accepts_positive_finite_values_up_to_task_limit(value: float) -> None:
    assert ToolTimeoutPolicy(value).timeout_s == float(value)


@pytest.mark.parametrize(
    "value",
    [0, 0.0, -1, float("nan"), float("inf"), float("-inf"), 300.0001, True, False, "1"],
)
def test_policy_rejects_invalid_values(value: object) -> None:
    with pytest.raises(ToolTimeoutPolicyError):
        ToolTimeoutPolicy(value)  # type: ignore[arg-type]


def test_allowed_requirements_tool_runs_and_returns_value() -> None:
    assert run_tool_with_timeout(
        role_id="requirements_engineer",
        tool_id="project_read",
        operation=_return_42,
        timeout_s=1.0,
    ) == 42


def test_allowed_engineering_tool_runs() -> None:
    assert run_tool_with_timeout(
        role_id="uav_systems_engineer",
        tool_id="calculator",
        operation=_return_42,
        timeout_s=1.0,
    ) == 42


def test_allowed_reviewer_tool_runs() -> None:
    assert run_tool_with_timeout(
        role_id="reviewer",
        tool_id="issue_create",
        operation=_return_ok,
        timeout_s=1.0,
    ) == "ok"


def test_denied_tool_is_rejected_before_operation_runs(tmp_path: Path) -> None:
    marker = tmp_path / "denied-ran"

    def operation() -> None:
        marker.write_text("bad")

    with pytest.raises(ToolNotAllowedError):
        run_tool_with_timeout(
            role_id="reviewer",
            tool_id="draft_write",
            operation=operation,
            timeout_s=1.0,
        )
    assert not marker.exists()


def test_coordinator_empty_allowlist_is_enforced() -> None:
    with pytest.raises(ToolNotAllowedError):
        run_tool_with_timeout(
            role_id="coordinator",
            tool_id="project_read",
            operation=_return_42,
            timeout_s=1.0,
        )


def test_unknown_role_fails_closed() -> None:
    with pytest.raises(RoleToolAllowlistError, match="role is not registered"):
        run_tool_with_timeout(
            role_id="future_role",
            tool_id="project_read",
            operation=_return_42,
            timeout_s=1.0,
        )


def test_unknown_tool_fails_closed() -> None:
    with pytest.raises(RoleToolAllowlistError, match="tool is not registered"):
        run_tool_with_timeout(
            role_id="reviewer",
            tool_id="future_tool",
            operation=_return_42,
            timeout_s=1.0,
        )


def test_timeout_raises_canonical_tool_failure() -> None:
    started = time.monotonic()
    with pytest.raises(ToolTimedOutError) as caught:
        run_tool_with_timeout(
            role_id="requirements_engineer",
            tool_id="project_read",
            operation=_sleep_long,
            timeout_s=0.05,
        )
    elapsed = time.monotonic() - started
    assert elapsed < 1.0
    failure = caught.value.failure
    assert failure.failure_class is FailureClass.TOOL
    assert failure.code == "TOOL_TIMEOUT"
    assert failure.details["role_id"] == "requirements_engineer"
    assert failure.details["tool_id"] == "project_read"
    assert failure.details["timeout_s"] == 0.05


def test_timed_out_child_cannot_continue_delayed_side_effect(tmp_path: Path) -> None:
    marker = tmp_path / "late-side-effect"

    operation = functools.partial(_delayed_write, str(marker))

    with pytest.raises(ToolTimedOutError):
        run_tool_with_timeout(
            role_id="requirements_engineer",
            tool_id="project_read",
            operation=operation,
            timeout_s=0.03,
        )
    time.sleep(0.4)
    assert not marker.exists()


def test_child_exception_is_not_misclassified_as_timeout() -> None:
    with pytest.raises(ToolInvocationError, match="ValueError.*boom"):
        run_tool_with_timeout(
            role_id="requirements_engineer",
            tool_id="project_read",
            operation=_raise_value_error,
            timeout_s=1.0,
        )


def test_untransportable_result_fails_closed() -> None:
    with pytest.raises(ToolInvocationError, match="transport_error"):
        run_tool_with_timeout(
            role_id="requirements_engineer",
            tool_id="project_read",
            operation=_unpickleable_result,
            timeout_s=1.0,
        )


def test_daemon_tool_child_cannot_spawn_nested_processes() -> None:
    with pytest.raises(ToolInvocationError, match="daemonic processes"):
        run_tool_with_timeout(
            role_id="requirements_engineer",
            tool_id="project_read",
            operation=_spawn_nested_process,
            timeout_s=1.0,
        )


def test_non_callable_operation_fails_closed() -> None:
    wrapper = ToolTimeoutWrapper()
    with pytest.raises(TypeError, match="operation must be callable"):
        wrapper.run(
            role_id="requirements_engineer",
            tool_id="project_read",
            operation=123,  # type: ignore[arg-type]
            policy=ToolTimeoutPolicy(1.0),
        )


def test_wrong_policy_type_fails_closed() -> None:
    wrapper = ToolTimeoutWrapper()
    with pytest.raises(ToolTimeoutPolicyError, match="policy must"):
        wrapper.run(
            role_id="requirements_engineer",
            tool_id="project_read",
            operation=_return_42,
            policy=1.0,  # type: ignore[arg-type]
        )


def test_timeout_cleanup_leaves_no_live_ruav_tool_children() -> None:
    before = {p.pid for p in multiprocessing.active_children()}
    with pytest.raises(ToolTimedOutError):
        run_tool_with_timeout(
            role_id="requirements_engineer",
            tool_id="project_read",
            operation=_sleep_long,
            timeout_s=0.03,
        )
    time.sleep(0.05)
    leaked = [
        p for p in multiprocessing.active_children()
        if p.pid not in before and p.name.startswith("ruav-tool-")
    ]
    assert leaked == []


def test_success_cleanup_leaves_no_live_ruav_tool_children() -> None:
    before = {p.pid for p in multiprocessing.active_children()}
    assert run_tool_with_timeout(
        role_id="requirements_engineer",
        tool_id="project_read",
        operation=_return_42,
        timeout_s=1.0,
    ) == 42
    time.sleep(0.02)
    leaked = [
        p for p in multiprocessing.active_children()
        if p.pid not in before and p.name.startswith("ruav-tool-")
    ]
    assert leaked == []


def test_wrapper_exposes_no_command_shell_path_or_network_parameters() -> None:
    params = set(inspect.signature(ToolTimeoutWrapper.run).parameters)
    forbidden = {"command", "argv", "shell", "path", "url", "network", "px4", "mavsdk"}
    assert not (params & forbidden)


def test_timeout_module_does_not_import_subprocess_socket_px4_or_mavsdk() -> None:
    import ai.tools.timeout as module
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


def test_x10_1_and_x10_2_registries_are_unchanged() -> None:
    assert len(DEFAULT_TOOL_REGISTRY.tool_ids()) == 8
    assert len(DEFAULT_ROLE_TOOL_ALLOWLIST_REGISTRY.role_ids()) == 4
    assert DEFAULT_ROLE_TOOL_ALLOWLIST_REGISTRY.allowed_tool_ids("coordinator") == ()


def test_x10_3_does_not_add_later_gate_persistence_or_result_contracts() -> None:
    wrapper = ToolTimeoutWrapper()
    for name in (
        "audit", "audit_log", "persist", "path_policy", "result_schema",
        "structured_result", "retry", "run_loop", "execute_command",
    ):
        assert not hasattr(wrapper, name)
