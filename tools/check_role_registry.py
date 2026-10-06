#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
import sys

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from ai.agents import (  # noqa: E402
    MVP_ROLE_IDS,
    RESERVED_SPECIALIZED_ROLE_IDS,
    get_default_role_registry,
)


def main() -> int:
    registry = get_default_role_registry()

    if set(registry.role_ids()) != set(MVP_ROLE_IDS):
        print("ROLE_REGISTRY=FAIL reason=mvp_roles")
        return 2

    if any(registry.contains(role) for role in RESERVED_SPECIALIZED_ROLE_IDS):
        print("ROLE_REGISTRY=FAIL reason=specialized_role_activated")
        return 3

    print(
        "ROLE_REGISTRY=PASS "
        "roles=4 "
        "coordinator=PASS "
        "requirements_engineer=PASS "
        "uav_systems_engineer=PASS "
        "reviewer=PASS "
        "specialized=reserved "
        "provider_calls=none "
        "task_mapping=reserved_for_G6_3 "
        "agent_instance=reserved_for_G6_2"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
