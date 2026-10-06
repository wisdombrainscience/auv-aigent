from __future__ import annotations

from dataclasses import fields
import ast
from pathlib import Path
from types import MappingProxyType
import time

import pytest

from ai.contracts.failures import FailureClass, FailureDescriptor
from ai.tools import (
    TOOL_RESULT_CONTRACT_VERSION,
    ToolInvocationError,
    ToolNotAllowedError,
    ToolPathDeniedError,
    ToolPathNotApplicableError,
    ToolPathRestrictionError,
    ToolResult,
    ToolResultError,
    ToolResultStatus,
    ToolTimedOutError,
    ToolTimeoutPolicyError,
    parse_tool_result,
    run_tool_structured,
    tool_failure_from_exception,
)


def _return_nested_json():
    return {"answer": 42, "items": [True, None, 1.25], "meta": {"unit": "1"}}


def _return_nonfinite():
    return float("nan")


def _return_bytes():
    return b"not-json"


def _raise_value_error():
    raise ValueError("boom")


def _sleep_long():
    time.sleep(1.0)
    return "late"


def _tool_failure(code: str = "TOOL_TEST_FAILURE") -> FailureDescriptor:
    return FailureDescriptor.from_dict(
        {
            "class": "TOOL",
            "code": code,
            "message": "Synthetic tool failure.",
            "details": {"test": True},
        }
    )


def test_contract_version_is_frozen() -> None:
    assert TOOL_RESULT_CONTRACT_VERSION == "1.0"


def test_wire_fields_are_exact_and_minimal() -> None:
    assert tuple(field.name for field in fields(ToolResult)) == (
        "status",
        "role_id",
        "tool_id",
        "output",
        "failure",
    )


def test_status_vocabulary_is_exact() -> None:
    assert tuple(item.value for item in ToolResultStatus) == ("SUCCESS", "FAILURE")


def test_success_roundtrip_freezes_nested_json() -> None:
    result = ToolResult.success(
        role_id="uav_systems_engineer",
        tool_id="calculator",
        output={"answer": 42, "items": [True, None], "meta": {"unit": "1"}},
    )
    assert result.status is ToolResultStatus.SUCCESS
    assert isinstance(result.output, MappingProxyType)
    assert result.failure is None
    raw = result.to_dict()
    assert raw == {
        "status": "SUCCESS",
        "role_id": "uav_systems_engineer",
        "tool_id": "calculator",
        "output": {"answer": 42, "items": [True, None], "meta": {"unit": "1"}},
        "failure": None,
    }
    assert parse_tool_result(raw) == result


def test_success_may_carry_json_null_output() -> None:
    result = ToolResult.success(
        role_id="requirements_engineer",
        tool_id="project_read",
        output=None,
    )
    assert result.status is ToolResultStatus.SUCCESS
    assert result.output is None
    assert result.failure is None


def test_failure_roundtrip_uses_c18_failure_descriptor() -> None:
    failure = _tool_failure()
    result = ToolResult.failed(
        role_id="reviewer",
        tool_id="issue_create",
        failure=failure,
    )
    assert result.status is ToolResultStatus.FAILURE
    assert result.output is None
    assert result.failure == failure
    assert parse_tool_result(result.to_dict()) == result


def test_success_rejects_non_null_failure() -> None:
    with pytest.raises(ToolResultError, match="SUCCESS"):
        ToolResult.from_dict(
            {
                "status": "SUCCESS",
                "role_id": "reviewer",
                "tool_id": "issue_create",
                "output": None,
                "failure": _tool_failure().to_dict(),
            }
        )


def test_failure_requires_failure_descriptor() -> None:
    with pytest.raises(ToolResultError, match="must include failure"):
        ToolResult.from_dict(
            {
                "status": "FAILURE",
                "role_id": "reviewer",
                "tool_id": "issue_create",
                "output": None,
                "failure": None,
            }
        )


def test_failure_requires_null_output() -> None:
    with pytest.raises(ToolResultError, match="output=null"):
        ToolResult.from_dict(
            {
                "status": "FAILURE",
                "role_id": "reviewer",
                "tool_id": "issue_create",
                "output": {"bad": True},
                "failure": _tool_failure().to_dict(),
            }
        )


def test_failure_must_use_tool_class() -> None:
    failure = FailureDescriptor.from_dict(
        {
            "class": "TASK",
            "code": "TASK_TEST_FAILURE",
            "message": "Synthetic task failure.",
            "details": {},
        }
    )
    with pytest.raises(ToolResultError, match="TOOL failure class"):
        ToolResult.failed(role_id="reviewer", tool_id="issue_create", failure=failure)


@pytest.mark.parametrize("status", ["PASS", "FAIL", "OK", "ERROR", " success ", 1, None])
def test_invalid_status_is_rejected(status: object) -> None:
    with pytest.raises(ToolResultError):
        ToolResult.from_dict(
            {
                "status": status,
                "role_id": "reviewer",
                "tool_id": "issue_create",
                "output": None,
                "failure": None,
            }
        )


def test_exact_wire_keys_are_enforced() -> None:
    base = {
        "status": "SUCCESS",
        "role_id": "reviewer",
        "tool_id": "issue_create",
        "output": None,
        "failure": None,
    }
    missing = dict(base)
    missing.pop("output")
    with pytest.raises(ToolResultError, match="missing=output"):
        ToolResult.from_dict(missing)
    extra = dict(base, audit_id="x")
    with pytest.raises(ToolResultError, match="extra=audit_id"):
        ToolResult.from_dict(extra)


@pytest.mark.parametrize(
    "value",
    [float("nan"), float("inf"), float("-inf"), b"x", {1, 2}, object()],
)
def test_output_requires_strict_json(value: object) -> None:
    with pytest.raises(ToolResultError):
        ToolResult.success(
            role_id="uav_systems_engineer",
            tool_id="calculator",
            output=value,
        )


def test_output_rejects_non_string_and_empty_mapping_keys() -> None:
    with pytest.raises(ToolResultError, match="non-string"):
        ToolResult.success(
            role_id="uav_systems_engineer",
            tool_id="calculator",
            output={1: "bad"},
        )
    with pytest.raises(ToolResultError, match="empty object key"):
        ToolResult.success(
            role_id="uav_systems_engineer",
            tool_id="calculator",
            output={"": "bad"},
        )


def test_role_and_tool_ids_must_be_clean_tokens() -> None:
    with pytest.raises(ToolResultError, match="role_id"):
        ToolResult.success(role_id=" reviewer", tool_id="issue_create", output=None)
    with pytest.raises(ToolResultError, match="tool_id"):
        ToolResult.success(role_id="reviewer", tool_id="issue create", output=None)


def test_structured_runner_returns_success_envelope() -> None:
    result = run_tool_structured(
        role_id="uav_systems_engineer",
        tool_id="calculator",
        operation=_return_nested_json,
        timeout_s=5.0,
    )
    assert result.status is ToolResultStatus.SUCCESS
    assert result.failure is None
    assert result.to_dict()["output"]["answer"] == 42


def test_structured_runner_normalizes_known_deny() -> None:
    result = run_tool_structured(
        role_id="reviewer",
        tool_id="calculator",
        operation=_return_nested_json,
        timeout_s=5.0,
    )
    assert result.status is ToolResultStatus.FAILURE
    assert result.failure is not None
    assert result.failure.code == "TOOL_NOT_ALLOWED"
    assert result.failure.details["reason"] == "NOT_ALLOWLISTED"


def test_structured_runner_normalizes_unknown_role() -> None:
    result = run_tool_structured(
        role_id="future_role",
        tool_id="project_read",
        operation=_return_nested_json,
        timeout_s=5.0,
    )
    assert result.status is ToolResultStatus.FAILURE
    assert result.failure is not None
    assert result.failure.code == "TOOL_NOT_ALLOWED"
    assert result.failure.details["reason"] == "UNKNOWN_OR_INVALID_ROLE_TOOL"


def test_structured_runner_normalizes_unknown_tool() -> None:
    result = run_tool_structured(
        role_id="reviewer",
        tool_id="future_tool",
        operation=_return_nested_json,
        timeout_s=5.0,
    )
    assert result.failure is not None
    assert result.failure.code == "TOOL_NOT_ALLOWED"


def test_structured_runner_preserves_timeout_failure() -> None:
    result = run_tool_structured(
        role_id="requirements_engineer",
        tool_id="project_read",
        operation=_sleep_long,
        timeout_s=0.03,
    )
    assert result.status is ToolResultStatus.FAILURE
    assert result.failure is not None
    assert result.failure.failure_class is FailureClass.TOOL
    assert result.failure.code == "TOOL_TIMEOUT"
    assert result.failure.details["timeout_s"] == 0.03


def test_structured_runner_normalizes_child_exception() -> None:
    result = run_tool_structured(
        role_id="requirements_engineer",
        tool_id="project_read",
        operation=_raise_value_error,
        timeout_s=5.0,
    )
    assert result.failure is not None
    assert result.failure.code == "TOOL_INVOCATION_FAILED"
    assert result.output is None


def test_structured_runner_normalizes_invalid_timeout_policy() -> None:
    result = run_tool_structured(
        role_id="requirements_engineer",
        tool_id="project_read",
        operation=_return_nested_json,
        timeout_s=0.0,
    )
    assert result.failure is not None
    assert result.failure.code == "TOOL_POLICY_INVALID"


@pytest.mark.parametrize("operation", [_return_nonfinite, _return_bytes])
def test_structured_runner_converts_unstructured_output_to_failure(operation) -> None:
    result = run_tool_structured(
        role_id="uav_systems_engineer",
        tool_id="calculator",
        operation=operation,
        timeout_s=5.0,
    )
    assert result.status is ToolResultStatus.FAILURE
    assert result.failure is not None
    assert result.failure.code == "TOOL_RESULT_INVALID"
    assert result.output is None


def test_path_denied_error_can_be_normalized_without_leaking_path_text() -> None:
    result = tool_failure_from_exception(
        role_id="requirements_engineer",
        tool_id="project_read",
        error=ToolPathDeniedError("/secret/host/path"),
    )
    assert result.failure is not None
    assert result.failure.code == "TOOL_PATH_DENIED"
    assert "/secret/host/path" not in str(result.to_dict())


def test_path_not_applicable_is_distinct_failure_code() -> None:
    result = tool_failure_from_exception(
        role_id="uav_systems_engineer",
        tool_id="calculator",
        error=ToolPathNotApplicableError("pathless"),
    )
    assert result.failure is not None
    assert result.failure.code == "TOOL_PATH_NOT_APPLICABLE"


def test_generic_path_contract_failure_is_normalized() -> None:
    result = tool_failure_from_exception(
        role_id="requirements_engineer",
        tool_id="project_read",
        error=ToolPathRestrictionError("invalid"),
    )
    assert result.failure is not None
    assert result.failure.code == "TOOL_PATH_INVALID"


def test_known_timeout_error_preserves_original_descriptor() -> None:
    failure = FailureDescriptor.from_dict(
        {
            "class": "TOOL",
            "code": "TOOL_TIMEOUT",
            "message": "Tool execution exceeded its bounded timeout.",
            "details": {"role_id": "reviewer", "tool_id": "project_read", "timeout_s": 1.0},
        }
    )
    result = tool_failure_from_exception(
        role_id="reviewer",
        tool_id="project_read",
        error=ToolTimedOutError(failure),
    )
    assert result.failure == failure


def test_known_invocation_error_is_normalized() -> None:
    result = tool_failure_from_exception(
        role_id="reviewer",
        tool_id="project_read",
        error=ToolInvocationError("internal child details"),
    )
    assert result.failure is not None
    assert result.failure.code == "TOOL_INVOCATION_FAILED"
    assert "internal child details" not in str(result.to_dict())


def test_known_timeout_policy_error_is_normalized() -> None:
    result = tool_failure_from_exception(
        role_id="reviewer",
        tool_id="project_read",
        error=ToolTimeoutPolicyError("bad"),
    )
    assert result.failure is not None
    assert result.failure.code == "TOOL_POLICY_INVALID"


def test_unknown_exception_is_not_swallowed() -> None:
    with pytest.raises(TypeError, match="unsupported tool operational error"):
        tool_failure_from_exception(
            role_id="reviewer",
            tool_id="project_read",
            error=RuntimeError("unexpected programming error"),
        )


def test_x105_module_exposes_no_shell_subprocess_network_or_px4_imports() -> None:
    source_path = Path(__file__).resolve().parents[2] / "ai" / "tools" / "results.py"
    tree = ast.parse(source_path.read_text(encoding="utf-8"))
    forbidden = {"subprocess", "socket", "requests", "mavsdk", "px4", "pymavlink"}
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0].lower() for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0].lower())
    assert imported.isdisjoint(forbidden)


def test_x105_contract_has_no_audit_or_persistence_fields() -> None:
    assert {field.name for field in fields(ToolResult)}.isdisjoint(
        {"audit_id", "timestamp", "log_path", "project_id", "task_id", "persisted"}
    )
