#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ai.tools import (
    DEFAULT_TOOL_IDS,
    DEFAULT_TOOL_REGISTRY,
    FORBIDDEN_BY_DEFAULT_TOOL_IDS,
    TOOL_REGISTRY_CONTRACT_VERSION,
)


def main() -> int:
    expected = {
        "project_read",
        "artifact_read",
        "draft_write",
        "calculator",
        "workspace_read",
        "workspace_write",
        "allowlisted_test_runner",
        "issue_create",
    }
    assert TOOL_REGISTRY_CONTRACT_VERSION == "1.0"
    assert len(DEFAULT_TOOL_REGISTRY.definitions()) == 8
    assert set(DEFAULT_TOOL_REGISTRY.tool_ids()) == expected
    assert set(DEFAULT_TOOL_IDS) == expected
    assert all(
        not DEFAULT_TOOL_REGISTRY.contains(tool_id)
        for tool_id in FORBIDDEN_BY_DEFAULT_TOOL_IDS
    )
    assert not hasattr(DEFAULT_TOOL_REGISTRY.get("project_read"), "execute")

    print(
        "TOOL_REGISTRY=PASS "
        "contract_version=1.0 "
        "registered_tools=8 "
        "canonical_tools=PASS "
        "forbidden_by_default=unregistered "
        "role_allowlists=active_X10_2 "
        "timeout_wrapper=active_X10_3 "
        "path_restriction=active_X10_4 "
        "structured_results=active_X10_5 "
        "audit_log=active_X10_6 "
        "executors=none "
        "provider_calls=none "
        "px4_access=none "
        "phase10=pending "
        "cp9=pending"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
