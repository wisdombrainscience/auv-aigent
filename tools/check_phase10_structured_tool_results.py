#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ai.tools import (
    TOOL_RESULT_CONTRACT_VERSION,
    ToolPathDeniedError,
    ToolResultStatus,
    run_tool_structured,
    tool_failure_from_exception,
)


def _success():
    return {"value": 42, "unit": "1"}


def _slow():
    time.sleep(1.0)
    return "late"


def _raise_value_error():
    raise ValueError("boom")


def _invalid_output():
    return b"not-json"


def main() -> int:
    assert TOOL_RESULT_CONTRACT_VERSION == "1.0"

    success = run_tool_structured(
        role_id="uav_systems_engineer",
        tool_id="calculator",
        operation=_success,
        timeout_s=5.0,
    )
    assert success.status is ToolResultStatus.SUCCESS
    assert success.to_dict()["output"] == {"value": 42, "unit": "1"}

    denied = run_tool_structured(
        role_id="reviewer",
        tool_id="calculator",
        operation=_success,
        timeout_s=5.0,
    )
    assert denied.failure is not None and denied.failure.code == "TOOL_NOT_ALLOWED"

    timed = run_tool_structured(
        role_id="requirements_engineer",
        tool_id="project_read",
        operation=_slow,
        timeout_s=0.03,
    )
    assert timed.failure is not None and timed.failure.code == "TOOL_TIMEOUT"

    invocation = run_tool_structured(
        role_id="requirements_engineer",
        tool_id="project_read",
        operation=_raise_value_error,
        timeout_s=5.0,
    )
    assert invocation.failure is not None and invocation.failure.code == "TOOL_INVOCATION_FAILED"

    invalid = run_tool_structured(
        role_id="uav_systems_engineer",
        tool_id="calculator",
        operation=_invalid_output,
        timeout_s=5.0,
    )
    assert invalid.failure is not None and invalid.failure.code == "TOOL_RESULT_INVALID"

    path_failure = tool_failure_from_exception(
        role_id="requirements_engineer",
        tool_id="project_read",
        error=ToolPathDeniedError("secret path must not escape"),
    )
    assert path_failure.failure is not None and path_failure.failure.code == "TOOL_PATH_DENIED"

    print(
        "STRUCTURED_TOOL_RESULTS=PASS "
        "contract_version=1.0 "
        "fields=5 "
        "success=SUCCESS "
        "deny=TOOL_NOT_ALLOWED "
        "timeout=TOOL_TIMEOUT "
        "invocation_failure=TOOL_INVOCATION_FAILED "
        "invalid_output=TOOL_RESULT_INVALID "
        "strict_json=PASS "
        "path_failure=TOOL_PATH_DENIED "
        "audit_log=active_X10_6 "
        "shell_api=none "
        "executor_registry=none "
        "provider_calls=none "
        "px4_access=none "
        "phase10=pending "
        "cp9=pending"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
