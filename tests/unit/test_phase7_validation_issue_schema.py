from __future__ import annotations

import hashlib
import json
from types import MappingProxyType

import pytest

from ai.contracts import (
    MAX_VALIDATION_ISSUES,
    VALIDATION_ISSUE_CONTRACT_VERSION,
    VALIDATION_ISSUE_FINGERPRINT_ALGORITHM,
    ValidationCheck,
    ValidationIssue,
    ValidationIssueError,
    ValidationIssueSet,
    ValidationResult,
    ValidationStatus,
    normalize_validation_issues,
    validation_issue_fingerprint,
)


def check(
    check_id: str = "constraint.twr.min",
    *,
    status: str = "FAIL",
    field: str | None = "propulsion.thrust_to_weight",
    reason: str | None = "constraint twr.min failed",
    details: dict | None = None,
) -> ValidationCheck:
    return ValidationCheck.from_dict(
        {
            "check_id": check_id,
            "status": status,
            "field": field,
            "reason": reason,
            "details": details
            if details is not None
            else {
                "observed_value": 1.2,
                "required_value": 1.5,
                "unit": "1",
            },
        }
    )


def fail_result(*checks: ValidationCheck) -> ValidationResult:
    return ValidationResult.from_checks(checks or (check(),))


def test_contract_constants_are_explicit() -> None:
    assert VALIDATION_ISSUE_CONTRACT_VERSION == "1.0"
    assert VALIDATION_ISSUE_FINGERPRINT_ALGORITHM == "sha256"
    assert MAX_VALIDATION_ISSUES == 256


def test_failed_check_normalizes_to_one_issue() -> None:
    result = fail_result()
    issues = normalize_validation_issues(result)
    assert len(issues.issues) == 1
    issue = issues.issues[0]
    assert issue.check_id == "constraint.twr.min"
    assert issue.field == "propulsion.thrust_to_weight"
    assert issue.reason == "constraint twr.min failed"
    assert issue.to_dict()["details"]["observed_value"] == 1.2


def test_pass_result_yields_empty_issue_set() -> None:
    result = ValidationResult.from_checks(
        (
            check(
                status="PASS",
                reason=None,
            ),
        )
    )
    issues = normalize_validation_issues(result)
    assert issues == ValidationIssueSet(issues=())
    assert issues.to_dict() == {"issues": []}


def test_mixed_result_emits_only_failed_checks_in_declared_order() -> None:
    result = ValidationResult.from_checks(
        (
            check("a", status="PASS", field="a", reason=None),
            check("b", field="b", reason="b failed"),
            check("c", status="PASS", field="c", reason=None),
            check("d", field="d", reason="d failed"),
        )
    )
    issues = normalize_validation_issues(result)
    assert [item.check_id for item in issues.issues] == ["b", "d"]


def test_fingerprint_is_sha256_lowercase_hex() -> None:
    fingerprint = validation_issue_fingerprint(
        check_id="constraint.mass.max",
        field="mass.total_kg",
        reason="constraint failed",
    )
    prefix, digest = fingerprint.split(":", 1)
    assert prefix == "sha256"
    assert len(digest) == 64
    assert digest == digest.lower()
    int(digest, 16)


def test_fingerprint_matches_independent_canonical_json_calculation() -> None:
    payload = {
        "check_id": "constraint.mass.max",
        "field": "mass.total_kg",
        "reason": "constraint failed",
    }
    raw = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    expected = "sha256:" + hashlib.sha256(raw).hexdigest()
    assert validation_issue_fingerprint(**payload) == expected


def test_fingerprint_excludes_evidence_details() -> None:
    first = ValidationIssue.from_failed_check(
        check(details={"observed_value": 1.2, "required_value": 1.5})
    )
    second = ValidationIssue.from_failed_check(
        check(details={"observed_value": 1.3, "required_value": 1.5})
    )
    assert first.fingerprint == second.fingerprint
    assert first.to_dict()["details"] != second.to_dict()["details"]


@pytest.mark.parametrize(
    ("field", "reason"),
    [
        ("propulsion.twr", "constraint twr.min failed"),
        ("propulsion.thrust_to_weight", "different root cause"),
    ],
)
def test_fingerprint_changes_when_root_cause_identity_changes(field, reason) -> None:
    original = validation_issue_fingerprint(
        check_id="constraint.twr.min",
        field="propulsion.thrust_to_weight",
        reason="constraint twr.min failed",
    )
    changed = validation_issue_fingerprint(
        check_id="constraint.twr.min",
        field=field,
        reason=reason,
    )
    assert changed != original


def test_null_field_is_supported() -> None:
    issue = ValidationIssue.from_failed_check(
        check(field=None, reason="artifact-level validation failed")
    )
    assert issue.field is None


def test_issue_roundtrip_is_deterministic() -> None:
    original = ValidationIssue.from_failed_check(check())
    reopened = ValidationIssue.from_dict(original.to_dict())
    assert reopened == original
    assert reopened.to_dict() == original.to_dict()


def test_issue_set_roundtrip_is_deterministic() -> None:
    result = ValidationResult.from_checks(
        (
            check("constraint.mass.max", field="mass.total_kg", reason="mass failed"),
            check("constraint.twr.min", field="propulsion.twr", reason="twr failed"),
        )
    )
    original = normalize_validation_issues(result)
    reopened = ValidationIssueSet.from_dict(original.to_dict())
    assert reopened == original


def test_details_are_immutable_snapshots() -> None:
    issue = ValidationIssue.from_failed_check(
        check(details={"nested": {"values": [1, 2, 3]}})
    )
    assert isinstance(issue.details, MappingProxyType)
    with pytest.raises(TypeError):
        issue.details["x"] = 1  # type: ignore[index]
    nested = issue.details["nested"]
    assert isinstance(nested, MappingProxyType)


def test_normalization_does_not_mutate_source_validation_result() -> None:
    result = fail_result()
    before = result.to_dict()
    normalize_validation_issues(result)
    assert result.to_dict() == before


def test_only_fail_checks_can_be_converted_directly() -> None:
    passing = check(status="PASS", reason=None)
    with pytest.raises(ValidationIssueError, match="only from FAIL"):
        ValidationIssue.from_failed_check(passing)


def test_normalizer_requires_validation_result() -> None:
    with pytest.raises(ValidationIssueError, match="ValidationResult"):
        normalize_validation_issues({})  # type: ignore[arg-type]


def test_issue_schema_rejects_issue_id_field_reserved_for_later_persistence() -> None:
    payload = ValidationIssue.from_failed_check(check()).to_dict()
    payload["issue_id"] = "ISSUE-0001"
    with pytest.raises(ValidationIssueError, match="exactly the V7.10 fields"):
        ValidationIssue.from_dict(payload)


@pytest.mark.parametrize("extra", ["severity", "category", "required_action", "owner"])
def test_issue_schema_rejects_review_or_routing_fields(extra: str) -> None:
    payload = ValidationIssue.from_failed_check(check()).to_dict()
    payload[extra] = "x"
    with pytest.raises(ValidationIssueError, match="exactly the V7.10 fields"):
        ValidationIssue.from_dict(payload)


def test_tampered_fingerprint_is_rejected() -> None:
    payload = ValidationIssue.from_failed_check(check()).to_dict()
    payload["fingerprint"] = "sha256:" + ("0" * 64)
    with pytest.raises(ValidationIssueError, match="does not match"):
        ValidationIssue.from_dict(payload)


@pytest.mark.parametrize(
    "fingerprint",
    [
        "abc",
        "sha1:" + "0" * 40,
        "sha256:" + "A" * 64,
        "sha256:" + "0" * 63,
        " sha256:" + "0" * 64,
    ],
)
def test_malformed_fingerprints_are_rejected(fingerprint: str) -> None:
    payload = ValidationIssue.from_failed_check(check()).to_dict()
    payload["fingerprint"] = fingerprint
    with pytest.raises(ValidationIssueError):
        ValidationIssue.from_dict(payload)


def test_duplicate_fingerprint_in_issue_set_is_rejected() -> None:
    issue = ValidationIssue.from_failed_check(check()).to_dict()
    with pytest.raises(ValidationIssueError, match="duplicate"):
        ValidationIssueSet.from_dict({"issues": [issue, issue]})


def test_issue_set_is_bounded() -> None:
    checks = tuple(
        check(
            f"failure.{index}",
            field=f"field.{index}",
            reason=f"root cause {index}",
        )
        for index in range(MAX_VALIDATION_ISSUES + 1)
    )
    result = ValidationResult.from_checks(checks)
    with pytest.raises(ValidationIssueError, match="MAX_VALIDATION_ISSUES"):
        normalize_validation_issues(result)


def test_nested_details_roundtrip_preserves_strict_json_evidence() -> None:
    details = {
        "observed": 3.5,
        "required": 4,
        "unit": "kg",
        "flags": [True, False, None],
        "nested": {"source": "mass_budget"},
    }
    issue = ValidationIssue.from_failed_check(check(details=details))
    assert issue.to_dict()["details"] == details


def test_two_different_failed_checks_have_distinct_fingerprints() -> None:
    result = ValidationResult.from_checks(
        (
            check("constraint.mass.max", field="mass.total_kg", reason="mass failed"),
            check("constraint.distance.max", field="mission.distance_m", reason="distance failed"),
        )
    )
    issues = normalize_validation_issues(result)
    assert len({item.fingerprint for item in issues.issues}) == 2


def test_reason_is_required_by_issue_schema() -> None:
    payload = ValidationIssue.from_failed_check(check()).to_dict()
    payload["reason"] = ""
    with pytest.raises(ValidationIssueError, match="reason must not be empty"):
        ValidationIssue.from_dict(payload)


def test_schema_has_no_persistence_or_lifecycle_fields() -> None:
    keys = set(ValidationIssue.from_failed_check(check()).to_dict())
    assert keys == {"fingerprint", "check_id", "field", "reason", "details"}
    assert not {"issue_id", "artifact_state", "validation_result", "task_state"} & keys
