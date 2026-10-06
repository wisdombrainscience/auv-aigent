from __future__ import annotations

import json

import pytest

from ai.contracts.ids import CanonicalId, IdKind
from ai.contracts.review_result import (
    REVIEW_RESULT_CONTRACT_VERSION,
    ReviewIssue,
    ReviewResult,
    ReviewResultError,
    parse_review_result,
)


def example_issue(
    *,
    issue_id: str = "ISSUE-0041",
    fingerprint: str = "energy:battery.capacity_wh:endurance",
) -> dict[str, object]:
    return {
        "issue_id": issue_id,
        "fingerprint": fingerprint,
        "severity": "HIGH",
        "category": "ENERGY",
        "field": "battery.capacity_wh",
        "reason": "Available energy does not satisfy endurance constraint.",
        "required_action": "Recalculate energy budget.",
    }


def test_contract_version_is_explicit() -> None:
    assert REVIEW_RESULT_CONTRACT_VERSION == "1.0"


def test_empty_review_result_is_valid_but_not_a_pass_verdict() -> None:
    result = ReviewResult.from_dict({"issues": []})
    assert result.issues == ()
    assert result.to_dict() == {"issues": []}


def test_baseline_issue_round_trips() -> None:
    payload = {"issues": [example_issue()]}
    result = ReviewResult.from_dict(payload)
    assert result.to_dict() == payload
    assert json.loads(json.dumps(result.to_dict())) == payload


def test_parse_helper_returns_review_result() -> None:
    assert isinstance(
        parse_review_result({"issues": []}),
        ReviewResult,
    )


def test_issue_id_is_canonical_issue_namespace() -> None:
    result = ReviewIssue.from_dict(example_issue())
    assert result.issue_id == CanonicalId(IdKind.ISSUE, "ISSUE-0041")


@pytest.mark.parametrize(
    "bad_id",
    [
        "",
        "ISSUE-0000",
        "ISSUE-41",
        "issue-0041",
        "TASK-000041",
        "REVIEW-000041",
        41,
        None,
    ],
)
def test_invalid_issue_ids_are_rejected(bad_id: object) -> None:
    payload = example_issue()
    payload["issue_id"] = bad_id
    with pytest.raises(ReviewResultError):
        ReviewIssue.from_dict(payload)


@pytest.mark.parametrize(
    "field",
    [
        "issue_id",
        "fingerprint",
        "severity",
        "category",
        "field",
        "reason",
        "required_action",
    ],
)
def test_every_issue_field_is_required(field: str) -> None:
    payload = example_issue()
    del payload[field]
    with pytest.raises(ReviewResultError, match="missing="):
        ReviewIssue.from_dict(payload)


def test_unknown_issue_fields_are_rejected() -> None:
    payload = example_issue()
    payload["artifact_patch"] = {"battery.capacity_wh": 999}
    with pytest.raises(ReviewResultError, match="extra=artifact_patch"):
        ReviewIssue.from_dict(payload)


@pytest.mark.parametrize(
    "field",
    [
        "fingerprint",
        "severity",
        "category",
        "reason",
        "required_action",
    ],
)
@pytest.mark.parametrize("bad", ["", " ", " leading", "trailing ", 42, None])
def test_required_issue_text_fields_reject_malformed_values(
    field: str,
    bad: object,
) -> None:
    payload = example_issue()
    payload[field] = bad
    with pytest.raises(ReviewResultError):
        ReviewIssue.from_dict(payload)


def test_field_may_be_none_for_issue_not_bound_to_one_property() -> None:
    payload = example_issue()
    payload["field"] = None
    issue = ReviewIssue.from_dict(payload)
    assert issue.field is None


@pytest.mark.parametrize("bad", ["", " ", " field", "field ", 1, []])
def test_field_rejects_malformed_non_null_values(bad: object) -> None:
    payload = example_issue()
    payload["field"] = bad
    with pytest.raises(ReviewResultError):
        ReviewIssue.from_dict(payload)


@pytest.mark.parametrize(
    "extra",
    [
        {"verdict": "PASS"},
        {"status": "APPROVED"},
        {"artifact_patch": {}},
        {"replacement_artifact": {}},
    ],
)
def test_review_result_cannot_claim_authority_or_mutate_artifact(
    extra: dict[str, object],
) -> None:
    payload: dict[str, object] = {"issues": []}
    payload.update(extra)
    with pytest.raises(ReviewResultError, match="extra="):
        ReviewResult.from_dict(payload)


@pytest.mark.parametrize("bad", [None, {}, "issues", 1, True])
def test_issues_must_be_array(bad: object) -> None:
    with pytest.raises(ReviewResultError):
        ReviewResult.from_dict({"issues": bad})


def test_duplicate_issue_id_in_one_result_is_rejected() -> None:
    payload = {
        "issues": [
            example_issue(
                issue_id="ISSUE-0041",
                fingerprint="fp-1",
            ),
            example_issue(
                issue_id="ISSUE-0041",
                fingerprint="fp-2",
            ),
        ]
    }
    with pytest.raises(ReviewResultError, match="duplicate issue_id"):
        ReviewResult.from_dict(payload)


def test_duplicate_fingerprint_in_one_result_is_rejected() -> None:
    payload = {
        "issues": [
            example_issue(
                issue_id="ISSUE-0041",
                fingerprint="same-root-cause",
            ),
            example_issue(
                issue_id="ISSUE-0042",
                fingerprint="same-root-cause",
            ),
        ]
    }
    with pytest.raises(ReviewResultError, match="duplicate issue fingerprint"):
        ReviewResult.from_dict(payload)


def test_distinct_issue_ids_and_fingerprints_are_allowed() -> None:
    payload = {
        "issues": [
            example_issue(
                issue_id="ISSUE-0041",
                fingerprint="energy:capacity",
            ),
            example_issue(
                issue_id="ISSUE-0042",
                fingerprint="mass:budget",
            ),
        ]
    }
    result = ReviewResult.from_dict(payload)
    assert len(result.issues) == 2


def test_result_owns_immutable_issue_snapshot() -> None:
    raw_issue = example_issue()
    payload = {"issues": [raw_issue]}
    result = ReviewResult.from_dict(payload)

    raw_issue["reason"] = "mutated after parse"
    assert (
        result.to_dict()["issues"][0]["reason"]
        == "Available energy does not satisfy endurance constraint."
    )


def test_serialized_output_is_detached_from_internal_result() -> None:
    result = ReviewResult.from_dict(
        {"issues": [example_issue()]}
    )
    first = result.to_dict()
    first["issues"][0]["reason"] = "changed"

    second = result.to_dict()
    assert (
        second["issues"][0]["reason"]
        == "Available energy does not satisfy endurance constraint."
    )


def test_review_issue_is_frozen() -> None:
    issue = ReviewIssue.from_dict(example_issue())
    with pytest.raises(Exception):
        issue.reason = "changed"  # type: ignore[misc]
