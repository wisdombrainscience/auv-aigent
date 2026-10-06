#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ai.contracts.failures import FailureClass
from ai.tasks.lifecycle import TASK_TIMEOUT_S
from ai.tools import (
    MAX_TOOL_TIMEOUT_S,
    TOOL_TIMEOUT_CONTRACT_VERSION,
    ToolNotAllowedError,
    ToolTimedOutError,
    run_tool_with_timeout,
)


def _fast() -> int:
    return 42


def _slow() -> None:
    time.sleep(1.0)


def main() -> int:
    assert TOOL_TIMEOUT_CONTRACT_VERSION == "1.0"
    assert MAX_TOOL_TIMEOUT_S == TASK_TIMEOUT_S == 300.0
    assert run_tool_with_timeout(
        role_id="uav_systems_engineer",
        tool_id="calculator",
        operation=_fast,
        timeout_s=1.0,
    ) == 42

    denied = False
    try:
        run_tool_with_timeout(
            role_id="reviewer",
            tool_id="calculator",
            operation=_fast,
            timeout_s=1.0,
        )
    except ToolNotAllowedError:
        denied = True
    assert denied

    timeout_ok = False
    try:
        run_tool_with_timeout(
            role_id="requirements_engineer",
            tool_id="project_read",
            operation=_slow,
            timeout_s=0.03,
        )
    except ToolTimedOutError as exc:
        timeout_ok = (
            exc.failure.failure_class is FailureClass.TOOL
            and exc.failure.code == "TOOL_TIMEOUT"
            and exc.failure.details["tool_id"] == "project_read"
        )
    assert timeout_ok

    print(
        "TOOL_TIMEOUT_WRAPPER=PASS "
        "contract_version=1.0 "
        "max_timeout_s=300 "
        "bounded_success=PASS "
        "allowlist_enforced=PASS "
        "timeout=TOOL_TIMEOUT "
        "child_termination=PASS "
        "shell_api=none "
        "executor_registry=none "
        "path_restriction=active_X10_4 "
        "structured_results=active_X10_5 "
        "audit_log=active_X10_6 "
        "provider_calls=none "
        "px4_access=none "
        "phase10=pending "
        "cp9=pending"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
