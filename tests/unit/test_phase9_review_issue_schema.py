from __future__ import annotations

from pathlib import Path
import subprocess
import sys

import pytest

from ai.contracts import (
    REVIEW_ISSUE_CONTRACT_VERSION,
    ReviewIssue,
    ReviewResult,
    ReviewResultError,
    ValidationIssue,
    ValidationIssueError,
    parse_review_issue,
)
from ai.contracts.validation_issue import validation_issue_fingerprint


EXPECTED_FIELDS = {
    "issue_id",
    "fingerprint",
    "severity",
    "category",
    "field",
    "reason",
    "required_action",
}


def issue_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "issue_id": "ISSUE-0041",
        "fingerprint": "energy:capacity:available-vs-required",
        "severity": "HIGH",
        "category": "ENERGY",
        "field": "battery.capacity_wh",
        "reason": "Available energy does not satisfy endurance constraint.",
        "required_action": "Recalculate energy budget.",
    }
    payload.update(overrides)
    return payload


def test_r9_1_contract_version_is_frozen() -> None:
    assert REVIEW_ISSUE_CONTRACT_VERSION == "1.0"


def test_standalone_parser_reuses_c1_6_review_issue_wire_contract() -> None:
    issue = parse_review_issue(issue_payload())
    assert isinstance(issue, ReviewIssue)
    assert issue.to_dict() == issue_payload()


def test_r9_1_issue_has_exactly_seven_frozen_wire_fields() -> None:
    assert set(parse_review_issue(issue_payload()).to_dict()) == EXPECTED_FIELDS


def test_issue_id_must_use_canonical_issue_namespace() -> None:
    with pytest.raises(ReviewResultError, match="ISSUE ID namespace"):
        parse_review_issue(issue_payload(issue_id="TASK-000041"))


def test_fingerprint_is_required_but_algorithm_is_not_invented_by_r9_1() -> None:
    assert parse_review_issue(issue_payload(fingerprint="root-cause-key")).fingerprint == "root-cause-key"
    with pytest.raises(ReviewResultError, match="fingerprint must not be empty"):
        parse_review_issue(issue_payload(fingerprint=""))


def test_severity_and_category_vocabularies_remain_open_at_r9_1() -> None:
    issue = parse_review_issue(issue_payload(severity="CUSTOM", category="AIRFRAME"))
    assert issue.severity == "CUSTOM"
    assert issue.category == "AIRFRAME"


def test_field_remains_nullable_for_non_property_issue() -> None:
    issue = parse_review_issue(issue_payload(field=None))
    assert issue.field is None


@pytest.mark.parametrize(
    "extra",
    [
        "owner_role",
        "review_id",
        "artifact_id",
        "rework_task_id",
        "status",
        "attempt",
        "duplicate_of",
    ],
)
def test_r9_1_rejects_later_phase_routing_rework_or_lifecycle_fields(extra: str) -> None:
    payload = issue_payload()
    payload[extra] = "x"
    with pytest.raises(ReviewResultError, match="exactly the C1.6 fields"):
        parse_review_issue(payload)


def test_review_result_continues_to_embed_the_same_r9_1_issue_contract() -> None:
    issue = issue_payload()
    result = ReviewResult.from_dict({"issues": [issue]})
    assert result.to_dict() == {"issues": [issue]}


def test_review_issue_is_distinct_from_v7_10_validation_issue() -> None:
    review = issue_payload()
    with pytest.raises(ValidationIssueError):
        ValidationIssue.from_dict(review)

    validation = {
        "fingerprint": validation_issue_fingerprint(
            check_id="mass_budget.total",
            field="mass.total_kg",
            reason="mass exceeds constraint",
        ),
        "check_id": "mass_budget.total",
        "field": "mass.total_kg",
        "reason": "mass exceeds constraint",
        "details": {"observed": 4.5},
    }
    with pytest.raises(ReviewResultError):
        parse_review_issue(validation)


def test_parse_returns_immutable_snapshot_detached_from_input() -> None:
    raw = issue_payload()
    issue = parse_review_issue(raw)
    raw["reason"] = "mutated"
    assert issue.reason == "Available energy does not satisfy endurance constraint."


def test_r9_1_acceptance_probe_runs_directly_from_project_root() -> None:
    project_root = Path(__file__).resolve().parents[2]
    probe = project_root / "tools" / "check_phase9_review_issue_schema.py"
    proc = subprocess.run(
        [sys.executable, str(probe)],
        cwd=str(project_root),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    assert proc.returncode == 0, proc.stdout
    assert "REVIEW_ISSUE_SCHEMA=PASS" in proc.stdout
