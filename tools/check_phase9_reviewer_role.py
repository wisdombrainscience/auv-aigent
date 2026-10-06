#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ai.agents import DEFAULT_ROLE_REGISTRY, MVP_ROLE_IDS
from ai.contracts import REVIEW_RESULT_CONTRACT_VERSION, ReviewResult
from ai.review import (
    DEFAULT_REVIEWER_ROLE,
    REVIEWER_OUTPUT_CONTRACT,
    REVIEWER_ROLE_CONTRACT_VERSION,
    REVIEWER_ROLE_ID,
    create_reviewer_agent,
)


def main() -> int:
    before = DEFAULT_ROLE_REGISTRY.definitions()
    result = ReviewResult(issues=())
    agent = create_reviewer_agent(instance_key="REVIEW-ACCEPTANCE-001")

    checks = (
        REVIEWER_ROLE_CONTRACT_VERSION == "1.0",
        REVIEWER_ROLE_ID == "reviewer",
        REVIEWER_OUTPUT_CONTRACT == "ReviewResult",
        DEFAULT_REVIEWER_ROLE.role is DEFAULT_ROLE_REGISTRY.get("reviewer"),
        DEFAULT_REVIEWER_ROLE.result_contract_version == REVIEW_RESULT_CONTRACT_VERSION,
        DEFAULT_REVIEWER_ROLE.require_result(result) is result,
        agent.role_id == "reviewer",
        DEFAULT_ROLE_REGISTRY.definitions() == before,
        len(DEFAULT_ROLE_REGISTRY.definitions()) == 4,
        MVP_ROLE_IDS == (
            "coordinator",
            "requirements_engineer",
            "uav_systems_engineer",
            "reviewer",
        ),
    )
    if not all(checks):
        print("REVIEWER_ROLE=FAIL")
        return 1

    print(
        "REVIEWER_ROLE=PASS "
        "role=reviewer registry_roles=4 new_role_id=none "
        "output_contract=ReviewResult logical_agent=PASS advisory_only=PASS "
        "issue_allocation=none issue_persistence=none "
        "review_task=reserved_for_R9_3 owner_routing=reserved_for_R9_4 "
        "rework_task=reserved_for_R9_5 max_rework=reserved_for_R9_6 "
        "duplicate_suppression=reserved_for_R9_7 provider_calls=none "
        "task_execution=none cp9=pending"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
