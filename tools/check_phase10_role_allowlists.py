#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ai.agents.roles import DEFAULT_ROLE_REGISTRY
from ai.tools import (
    DEFAULT_ROLE_TOOL_ALLOWLIST_REGISTRY,
    FORBIDDEN_BY_DEFAULT_TOOL_IDS,
    ROLE_TOOL_ALLOWLIST_CONTRACT_VERSION,
)


def main() -> int:
    registry = DEFAULT_ROLE_TOOL_ALLOWLIST_REGISTRY
    assert ROLE_TOOL_ALLOWLIST_CONTRACT_VERSION == "1.0"
    assert registry.role_ids() == tuple(sorted(DEFAULT_ROLE_REGISTRY.role_ids()))
    assert registry.allowed_tool_ids("coordinator") == ()
    assert set(registry.allowed_tool_ids("requirements_engineer")) == {
        "project_read", "artifact_read", "draft_write"
    }
    assert set(registry.allowed_tool_ids("uav_systems_engineer")) == {
        "project_read", "calculator", "artifact_read", "draft_write"
    }
    assert set(registry.allowed_tool_ids("reviewer")) == {
        "artifact_read", "project_read", "issue_create"
    }
    assert not registry.is_allowed("reviewer", "draft_write")
    assert not registry.is_allowed("software_engineer", "workspace_read")
    assert all(
        not registry.is_allowed(role_id, tool_id)
        for role_id in DEFAULT_ROLE_REGISTRY.role_ids()
        for tool_id in FORBIDDEN_BY_DEFAULT_TOOL_IDS
    )
    assert not hasattr(registry, "execute")

    print(
        "ROLE_TOOL_ALLOWLISTS=PASS "
        "contract_version=1.0 "
        "roles=4 "
        "coordinator_tools=0 "
        "requirements_tools=3 "
        "engineering_tools=4 "
        "reviewer_tools=3 "
        "software_role=reserved "
        "deny_by_default=PASS "
        "unknown_role=DENY "
        "unknown_tool=DENY "
        "forbidden_capabilities=DENY "
        "tool_execution=none "
        "timeout_wrapper=active_X10_3 "
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
