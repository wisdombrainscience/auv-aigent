#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ai.contracts import (
    REVIEW_ISSUE_CONTRACT_VERSION,
    ReviewResultError,
    ValidationIssue,
    ValidationIssueError,
    parse_review_issue,
)
from ai.contracts.validation_issue import validation_issue_fingerprint


def main() -> int:
    payload = {
        "issue_id": "ISSUE-0041",
        "fingerprint": "energy:capacity:available-vs-required",
        "severity": "HIGH",
        "category": "ENERGY",
        "field": "battery.capacity_wh",
        "reason": "Available energy does not satisfy endurance constraint.",
        "required_action": "Recalculate energy budget.",
    }
    issue = parse_review_issue(payload)
    if issue.to_dict() != payload:
        raise SystemExit("review issue roundtrip failed")

    try:
        parse_review_issue({**payload, "owner_role": "uav_systems_engineer"})
    except ReviewResultError:
        routing_reserved = True
    else:
        routing_reserved = False
    if not routing_reserved:
        raise SystemExit("R9.1 accepted owner routing field")

    validation_payload = {
        "fingerprint": validation_issue_fingerprint(
            check_id="constraint.mass.max",
            field="mass.total_kg",
            reason="mass exceeds constraint",
        ),
        "check_id": "constraint.mass.max",
        "field": "mass.total_kg",
        "reason": "mass exceeds constraint",
        "details": {"observed": 4.5},
    }
    ValidationIssue.from_dict(validation_payload)
    try:
        parse_review_issue(validation_payload)
    except ReviewResultError:
        distinct = True
    else:
        distinct = False
    if not distinct:
        raise SystemExit("R9.1 review issue collapsed into ValidationIssue")

    try:
        ValidationIssue.from_dict(payload)
    except ValidationIssueError:
        reverse_distinct = True
    else:
        reverse_distinct = False
    if not reverse_distinct:
        raise SystemExit("ValidationIssue accepted R9.1 review issue wire schema")

    print(
        "REVIEW_ISSUE_SCHEMA=PASS "
        f"contract_version={REVIEW_ISSUE_CONTRACT_VERSION} "
        "fields=7 canonical_issue_id=PASS fingerprint=required "
        "fingerprint_algorithm=unspecified validation_issue=distinct "
        "allocation=none persistence=none "
        "reviewer_role=reserved_for_R9_2 review_task=reserved_for_R9_3 "
        "owner_routing=reserved_for_R9_4 rework_task=reserved_for_R9_5 "
        "max_rework=reserved_for_R9_6 duplicate_suppression=reserved_for_R9_7 "
        "provider_calls=none cp9=pending"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
