from __future__ import annotations

import json
import math

import pytest

from ai.contracts.failures import (
    FAILURE_CLASS_DESCRIPTIONS,
    FAILURE_TAXONOMY_CONTRACT_VERSION,
    FailureClass,
    FailureDescriptor,
    FailureTaxonomyError,
    describe_failure_class,
    parse_failure_class,
    parse_failure_descriptor,
    serialize_failure_class,
)


EXPECTED_CLASSES = [
    "INFRA",
    "PROVIDER",
    "MODEL",
    "TOOL",
    "PLAN",
    "TASK",
    "REVIEW",
    "VALIDATION",
    "SIMULATION",
    "VERIFICATION",
    "USER_INPUT",
]


def failure_payload(
    *,
    failure_class: str = "MODEL",
    code: str = "MODEL_INVALID_OUTPUT",
) -> dict[str, object]:
    return {
        "class": failure_class,
        "code": code,
        "message": "Structured model output did not satisfy the contract.",
        "details": {
            "contract": "AgentResult",
            "attempt": 1,
        },
    }


def test_contract_version_is_explicit() -> None:
    assert FAILURE_TAXONOMY_CONTRACT_VERSION == "1.0"


def test_failure_classes_match_frozen_domains_exactly() -> None:
    assert [item.value for item in FailureClass] == EXPECTED_CLASSES


@pytest.mark.parametrize("raw", EXPECTED_CLASSES)
def test_failure_class_round_trip(raw: str) -> None:
    failure_class = parse_failure_class(raw)
    assert serialize_failure_class(failure_class) == raw


@pytest.mark.parametrize(
    "raw",
    [
        "",
        " ",
        "MODEL ",
        " MODEL",
        "model",
        "USER INPUT",
        "USER-INPUT",
        "UNKNOWN",
        1,
        None,
    ],
)
def test_invalid_failure_classes_are_rejected(raw: object) -> None:
    with pytest.raises(FailureTaxonomyError):
        parse_failure_class(raw)  # type: ignore[arg-type]


def test_every_failure_class_has_description() -> None:
    assert set(FAILURE_CLASS_DESCRIPTIONS) == set(FailureClass)
    for item in FailureClass:
        text = describe_failure_class(item)
        assert text
        assert text == text.strip()


@pytest.mark.parametrize(
    ("failure_class", "code"),
    [
        ("INFRA", "INFRA_RESOURCE_LIMIT"),
        ("PROVIDER", "PROVIDER_UNAVAILABLE"),
        ("MODEL", "MODEL_INVALID_OUTPUT"),
        ("TOOL", "TOOL_TIMEOUT"),
        ("PLAN", "PLAN_INVALID"),
        ("TASK", "TASK_TIMEOUT"),
        ("REVIEW", "REVIEW_REWORK_EXHAUSTED"),
        ("VALIDATION", "VALIDATION_FAILED"),
        ("SIMULATION", "SIMULATION_FAILED"),
        ("VERIFICATION", "VERIFICATION_FAILED"),
        ("USER_INPUT", "USER_INPUT_REQUIRED"),
    ],
)
def test_category_prefixed_codes_are_accepted(
    failure_class: str,
    code: str,
) -> None:
    payload = failure_payload(
        failure_class=failure_class,
        code=code,
    )
    result = FailureDescriptor.from_dict(payload)
    assert result.failure_class.value == failure_class
    assert result.code == code


@pytest.mark.parametrize(
    ("failure_class", "code"),
    [
        ("MODEL", "PROVIDER_TIMEOUT"),
        ("TOOL", "TASK_TIMEOUT"),
        ("USER_INPUT", "INPUT_REQUIRED"),
        ("INFRA", "RESOURCE_LIMIT"),
        ("SIMULATION", "VERIFICATION_FAILED"),
    ],
)
def test_code_must_match_failure_class(
    failure_class: str,
    code: str,
) -> None:
    payload = failure_payload(
        failure_class=failure_class,
        code=code,
    )
    with pytest.raises(
        FailureTaxonomyError,
        match="must start with",
    ):
        FailureDescriptor.from_dict(payload)


@pytest.mark.parametrize(
    "code",
    [
        "",
        "MODEL",
        "model_invalid_output",
        "MODEL-INVALID-OUTPUT",
        " MODEL_INVALID_OUTPUT",
        "MODEL_INVALID_OUTPUT ",
        "MODEL__INVALID",
        "MODEL_INVALID output",
        42,
        None,
    ],
)
def test_malformed_codes_are_rejected(code: object) -> None:
    payload = failure_payload()
    payload["code"] = code
    with pytest.raises(FailureTaxonomyError):
        FailureDescriptor.from_dict(payload)


def test_model_invalid_output_example_round_trips() -> None:
    payload = failure_payload()
    result = FailureDescriptor.from_dict(payload)
    assert result.to_dict() == payload
    assert json.loads(json.dumps(result.to_dict())) == payload


def test_parse_helper_returns_descriptor() -> None:
    assert isinstance(
        parse_failure_descriptor(failure_payload()),
        FailureDescriptor,
    )


@pytest.mark.parametrize(
    "field",
    ["class", "code", "message", "details"],
)
def test_every_failure_field_is_required(field: str) -> None:
    payload = failure_payload()
    del payload[field]
    with pytest.raises(
        FailureTaxonomyError,
        match="missing=",
    ):
        FailureDescriptor.from_dict(payload)


@pytest.mark.parametrize(
    "extra",
    [
        {"retry": True},
        {"retry_count": 1},
        {"next_state": "READY"},
        {"automatic_action": "RETRY"},
        {"verdict": "PASS"},
    ],
)
def test_failure_descriptor_cannot_authorize_retry_or_verdict(
    extra: dict[str, object],
) -> None:
    payload = failure_payload()
    payload.update(extra)
    with pytest.raises(
        FailureTaxonomyError,
        match="extra=",
    ):
        FailureDescriptor.from_dict(payload)


@pytest.mark.parametrize(
    "bad",
    ["", " ", " leading", "trailing ", 1, None],
)
def test_message_must_be_clean_nonempty_text(bad: object) -> None:
    payload = failure_payload()
    payload["message"] = bad
    with pytest.raises(FailureTaxonomyError):
        FailureDescriptor.from_dict(payload)


@pytest.mark.parametrize(
    "bad",
    [None, [], "details", 1, True],
)
def test_details_must_be_object(bad: object) -> None:
    payload = failure_payload()
    payload["details"] = bad
    with pytest.raises(FailureTaxonomyError):
        FailureDescriptor.from_dict(payload)


@pytest.mark.parametrize(
    "bad_number",
    [math.nan, math.inf, -math.inf],
)
def test_details_reject_nonfinite_numbers(
    bad_number: float,
) -> None:
    payload = failure_payload()
    payload["details"] = {"value": bad_number}
    with pytest.raises(
        FailureTaxonomyError,
        match="non-finite",
    ):
        FailureDescriptor.from_dict(payload)


def test_details_reject_non_json_objects() -> None:
    payload = failure_payload()
    payload["details"] = {"bad": {1, 2, 3}}
    with pytest.raises(
        FailureTaxonomyError,
        match="unsupported JSON value type",
    ):
        FailureDescriptor.from_dict(payload)


def test_details_require_string_keys() -> None:
    payload = failure_payload()
    payload["details"] = {1: "bad"}
    with pytest.raises(
        FailureTaxonomyError,
        match="non-string object key",
    ):
        FailureDescriptor.from_dict(payload)


def test_details_snapshot_is_immutable_from_input_mutation() -> None:
    details = {
        "nested": {"attempt": 1},
    }
    payload = failure_payload()
    payload["details"] = details

    result = FailureDescriptor.from_dict(payload)
    details["nested"]["attempt"] = 999

    assert result.to_dict()["details"]["nested"]["attempt"] == 1


def test_serialized_details_are_detached() -> None:
    result = FailureDescriptor.from_dict(failure_payload())
    first = result.to_dict()
    first["details"]["attempt"] = 999

    second = result.to_dict()
    assert second["details"]["attempt"] == 1


def test_failure_descriptor_does_not_embed_retry_policy() -> None:
    result = FailureDescriptor.from_dict(failure_payload())
    assert not hasattr(result, "retry")
    assert not hasattr(result, "retryable")
    assert not hasattr(result, "retry_count")
    assert not hasattr(result, "next_state")
