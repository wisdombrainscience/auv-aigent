from __future__ import annotations

import json
import math

import pytest

from ai.contracts.validation_result import (
    VALIDATION_RESULT_CONTRACT_VERSION,
    ValidationCheck,
    ValidationResult,
    ValidationResultError,
    ValidationStatus,
    parse_validation_result,
)


def pass_check(
    *,
    check_id: str = "mass_budget",
) -> dict[str, object]:
    return {
        "check_id": check_id,
        "status": "PASS",
        "field": "mass.total_kg",
        "reason": None,
        "details": {
            "observed_kg": 4.2,
            "limit_kg": 5.0,
        },
    }


def fail_check(
    *,
    check_id: str = "energy_reserve",
) -> dict[str, object]:
    return {
        "check_id": check_id,
        "status": "FAIL",
        "field": "battery.reserve_percent",
        "reason": "Energy reserve is below the required minimum.",
        "details": {
            "observed_percent": 14.0,
            "required_min_percent": 20.0,
        },
    }


def test_contract_version_is_explicit() -> None:
    assert VALIDATION_RESULT_CONTRACT_VERSION == "1.0"


def test_single_pass_result_round_trips() -> None:
    payload = {
        "overall": "PASS",
        "checks": [pass_check()],
    }
    result = ValidationResult.from_dict(payload)
    assert result.overall is ValidationStatus.PASS
    assert result.to_dict() == payload
    assert json.loads(json.dumps(result.to_dict())) == payload


def test_any_failed_check_derives_overall_fail() -> None:
    payload = {
        "overall": "FAIL",
        "checks": [
            pass_check(),
            fail_check(),
        ],
    }
    result = ValidationResult.from_dict(payload)
    assert result.overall is ValidationStatus.FAIL


def test_from_checks_derives_pass() -> None:
    result = ValidationResult.from_checks(
        [ValidationCheck.from_dict(pass_check())]
    )
    assert result.overall is ValidationStatus.PASS


def test_from_checks_derives_fail() -> None:
    result = ValidationResult.from_checks(
        [
            ValidationCheck.from_dict(pass_check()),
            ValidationCheck.from_dict(fail_check()),
        ]
    )
    assert result.overall is ValidationStatus.FAIL


def test_parse_helper_returns_validation_result() -> None:
    result = parse_validation_result(
        {
            "overall": "PASS",
            "checks": [pass_check()],
        }
    )
    assert isinstance(result, ValidationResult)


@pytest.mark.parametrize(
    "status",
    [
        "WARN",
        "UNKNOWN",
        "SKIP",
        "PASS ",
        " pass",
        "pass",
        1,
        None,
    ],
)
def test_noncanonical_status_is_rejected(status: object) -> None:
    payload = pass_check()
    payload["status"] = status
    with pytest.raises(ValidationResultError):
        ValidationCheck.from_dict(payload)


@pytest.mark.parametrize(
    "overall",
    [
        "WARN",
        "UNKNOWN",
        "PASS ",
        "fail",
        1,
        None,
    ],
)
def test_noncanonical_overall_is_rejected(overall: object) -> None:
    with pytest.raises(ValidationResultError):
        ValidationResult.from_dict(
            {
                "overall": overall,
                "checks": [pass_check()],
            }
        )


def test_fail_check_requires_reason() -> None:
    payload = fail_check()
    payload["reason"] = None
    with pytest.raises(
        ValidationResultError,
        match="FAIL validation check must include",
    ):
        ValidationCheck.from_dict(payload)


@pytest.mark.parametrize("bad", ["", " ", " reason", "reason ", 42])
def test_reason_rejects_malformed_non_null_values(bad: object) -> None:
    payload = fail_check()
    payload["reason"] = bad
    with pytest.raises(ValidationResultError):
        ValidationCheck.from_dict(payload)


def test_pass_check_may_include_explanatory_reason() -> None:
    payload = pass_check()
    payload["reason"] = "Mass remains below the configured limit."
    check = ValidationCheck.from_dict(payload)
    assert check.reason == "Mass remains below the configured limit."


def test_field_may_be_none() -> None:
    payload = pass_check()
    payload["field"] = None
    check = ValidationCheck.from_dict(payload)
    assert check.field is None


@pytest.mark.parametrize("bad", ["", " ", " field", "field ", 1, []])
def test_field_rejects_malformed_non_null_values(bad: object) -> None:
    payload = pass_check()
    payload["field"] = bad
    with pytest.raises(ValidationResultError):
        ValidationCheck.from_dict(payload)


@pytest.mark.parametrize(
    "field",
    ["check_id", "status", "field", "reason", "details"],
)
def test_every_check_field_is_required(field: str) -> None:
    payload = pass_check()
    del payload[field]
    with pytest.raises(ValidationResultError, match="missing="):
        ValidationCheck.from_dict(payload)


def test_unknown_check_fields_are_rejected() -> None:
    payload = pass_check()
    payload["authority"] = "AI"
    with pytest.raises(ValidationResultError, match="extra=authority"):
        ValidationCheck.from_dict(payload)


@pytest.mark.parametrize(
    "extra",
    [
        {"reviewer": "AI"},
        {"approved": True},
        {"artifact_patch": {}},
        {"verdict_authority": "model"},
    ],
)
def test_validation_result_rejects_noncontract_authority_fields(
    extra: dict[str, object],
) -> None:
    payload: dict[str, object] = {
        "overall": "PASS",
        "checks": [pass_check()],
    }
    payload.update(extra)
    with pytest.raises(ValidationResultError, match="extra="):
        ValidationResult.from_dict(payload)


def test_empty_checks_cannot_claim_pass() -> None:
    with pytest.raises(
        ValidationResultError,
        match="at least one check",
    ):
        ValidationResult.from_dict(
            {
                "overall": "PASS",
                "checks": [],
            }
        )


@pytest.mark.parametrize("bad", [None, {}, "checks", 1, True])
def test_checks_must_be_array(bad: object) -> None:
    with pytest.raises(ValidationResultError):
        ValidationResult.from_dict(
            {
                "overall": "PASS",
                "checks": bad,
            }
        )


def test_duplicate_check_id_is_rejected() -> None:
    with pytest.raises(
        ValidationResultError,
        match="duplicate validation check_id",
    ):
        ValidationResult.from_dict(
            {
                "overall": "PASS",
                "checks": [
                    pass_check(check_id="schema"),
                    pass_check(check_id="schema"),
                ],
            }
        )


def test_declared_pass_cannot_hide_failed_child_check() -> None:
    with pytest.raises(
        ValidationResultError,
        match="inconsistent",
    ):
        ValidationResult.from_dict(
            {
                "overall": "PASS",
                "checks": [
                    pass_check(),
                    fail_check(),
                ],
            }
        )


def test_declared_fail_cannot_disagree_with_all_pass_checks() -> None:
    with pytest.raises(
        ValidationResultError,
        match="inconsistent",
    ):
        ValidationResult.from_dict(
            {
                "overall": "FAIL",
                "checks": [pass_check()],
            }
        )


@pytest.mark.parametrize("bad_number", [math.nan, math.inf, -math.inf])
def test_details_reject_nonfinite_numbers(bad_number: float) -> None:
    payload = pass_check()
    payload["details"] = {"observed": bad_number}
    with pytest.raises(ValidationResultError, match="non-finite"):
        ValidationCheck.from_dict(payload)


def test_details_reject_non_json_objects() -> None:
    payload = pass_check()
    payload["details"] = {"bad": {1, 2, 3}}
    with pytest.raises(
        ValidationResultError,
        match="unsupported JSON value type",
    ):
        ValidationCheck.from_dict(payload)


def test_details_require_string_keys() -> None:
    payload = pass_check()
    payload["details"] = {1: "bad"}
    with pytest.raises(
        ValidationResultError,
        match="non-string object key",
    ):
        ValidationCheck.from_dict(payload)


def test_details_snapshot_is_immutable_from_input_mutation() -> None:
    details = {
        "observed": 4.2,
        "nested": {"unit": "kg"},
    }
    payload = pass_check()
    payload["details"] = details

    check = ValidationCheck.from_dict(payload)
    details["observed"] = 99
    details["nested"]["unit"] = "lb"

    serialized = check.to_dict()
    assert serialized["details"]["observed"] == 4.2
    assert serialized["details"]["nested"]["unit"] == "kg"


def test_serialized_details_are_detached() -> None:
    result = ValidationResult.from_dict(
        {
            "overall": "PASS",
            "checks": [pass_check()],
        }
    )
    first = result.to_dict()
    first["checks"][0]["details"]["observed_kg"] = 999

    second = result.to_dict()
    assert second["checks"][0]["details"]["observed_kg"] == 4.2
