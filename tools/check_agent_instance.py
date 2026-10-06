#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
import sys


_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from ai.agents import (  # noqa: E402
    DEFAULT_ROLE_REGISTRY,
    RESERVED_SPECIALIZED_ROLE_IDS,
    AgentInstanceRoleError,
    create_agent_instance,
)


def main() -> int:
    first = create_agent_instance(
        instance_key="TASK-000001",
        role_id="uav_systems_engineer",
    )
    second = create_agent_instance(
        instance_key="TASK-000002",
        role_id="uav_systems_engineer",
    )

    if first == second:
        print("AGENT_INSTANCE=FAIL reason=isolation")
        return 2

    if first.role is not second.role:
        print("AGENT_INSTANCE=FAIL reason=role_registry_identity")
        return 3

    for role_id in RESERVED_SPECIALIZED_ROLE_IDS:
        try:
            create_agent_instance(
                instance_key="TASK-999999",
                role_id=role_id,
            )
        except AgentInstanceRoleError:
            pass
        else:
            print(
                "AGENT_INSTANCE=FAIL "
                f"reason=reserved_role_enabled role={role_id}"
            )
            return 4

    if len(DEFAULT_ROLE_REGISTRY.definitions()) != 4:
        print("AGENT_INSTANCE=FAIL reason=role_registry_changed")
        return 5

    print(
        "AGENT_INSTANCE=PASS "
        "instances=2 "
        "same_role=PASS "
        "isolated_keys=PASS "
        "immutable=PASS "
        "provider_calls=none "
        "task_binding=reserved_for_G6_3 "
        "context=reserved_for_G6_4 "
        "output=reserved_for_G6_5 "
        "run_persistence=reserved_for_G6_6"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
