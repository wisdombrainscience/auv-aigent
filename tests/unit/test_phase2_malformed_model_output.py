from __future__ import annotations

import json

import pytest

from ai.contracts.agent_result import AgentResult
from ai.providers.contracts import GenerateResponse
from ai.providers.model_output import (
    MAX_MODEL_OUTPUT_CONTENT_BYTES,
    MODEL_OUTPUT_CONTRACT_VERSION,
    ModelOutputError,
    ModelOutputReason,
    parse_agent_result_response,
)


def valid_payload() -> dict[str, object]:
    return {
        "status": "PROPOSED",
        "claims": [
            {
                "classification": "AI_PROPOSAL",
                "statement": "Use the proposed local engineering configuration.",
            }
        ],
        "assumptions": [],
        "calculations": [],
        "artifacts": [],
        "risks": [],
        "missing_information": [],
        "open_questions": [],
    }


def response(content: str, model: str = "local-model") -> GenerateResponse:
    return GenerateResponse(
        model=model,
        content=content,
        done=True,
    )


def assert_invalid(
    content: str,
    reason: ModelOutputReason,
) -> ModelOutputError:
    with pytest.raises(ModelOutputError) as caught:
        parse_agent_result_response(response(content))

    error = caught.value
    assert error.reason is reason
    failure = error.failure.to_dict()
    assert failure["class"] == "MODEL"
    assert failure["code"] == "MODEL_INVALID_OUTPUT"
    assert failure["details"]["contract"] == "AgentResult"
    assert failure["details"]["model"] == "local-model"
    assert failure["details"]["reason"] == reason.value
    assert failure["details"]["content_bytes"] == len(content.encode("utf-8"))
    assert "content" not in failure["details"]
    return error


def test_contract_version_and_bound_are_explicit() -> None:
    assert MODEL_OUTPUT_CONTRACT_VERSION == "1.0"
    assert MAX_MODEL_OUTPUT_CONTENT_BYTES == 8 * 1024 * 1024


def test_valid_agent_result_is_parsed() -> None:
    payload = valid_payload()
    result = parse_agent_result_response(
        response(json.dumps(payload, separators=(",", ":")))
    )
    assert isinstance(result, AgentResult)
    assert result.to_dict() == payload


def test_json_formatting_whitespace_is_allowed() -> None:
    payload = valid_payload()
    content = "\n  " + json.dumps(payload, indent=2) + "\n"
    result = parse_agent_result_response(response(content))
    assert result.to_dict() == payload


@pytest.mark.parametrize(
    "content",
    [
        "",
        "not-json",
        "{",
        "```json\\n{}\\n```",
        '{"status":"PROPOSED"} trailing',
        '{"x":NaN}',
        '{"x":Infinity}',
        '{"x":-Infinity}',
        '{"x":1e999}',
    ],
)
def test_invalid_json_fails_as_model_invalid_output(content: str) -> None:
    assert_invalid(content, ModelOutputReason.INVALID_JSON)


@pytest.mark.parametrize(
    "content",
    [
        "null",
        "[]",
        '"text"',
        "42",
        "true",
    ],
)
def test_non_object_top_level_is_rejected(content: str) -> None:
    assert_invalid(content, ModelOutputReason.TOP_LEVEL_NOT_OBJECT)


def test_duplicate_top_level_key_is_rejected() -> None:
    assert_invalid(
        '{"status":"PROPOSED","status":"PROPOSED"}',
        ModelOutputReason.DUPLICATE_JSON_KEY,
    )


def test_duplicate_nested_key_is_rejected() -> None:
    payload = (
        '{"status":"PROPOSED","claims":['
        '{"classification":"AI_PROPOSAL","statement":"a","statement":"b"}'
        '],"assumptions":[],"calculations":[],"artifacts":[],"risks":[],'
        '"missing_information":[],"open_questions":[]}'
    )
    assert_invalid(payload, ModelOutputReason.DUPLICATE_JSON_KEY)


@pytest.mark.parametrize(
    "mutator",
    [
        lambda p: p.pop("status"),
        lambda p: p.__setitem__("status", "PASS"),
        lambda p: p.__setitem__("extra", 1),
        lambda p: p.__setitem__("claims", "not-an-array"),
        lambda p: p.__setitem__(
            "claims",
            [{"classification": "UNKNOWN", "statement": "x"}],
        ),
        lambda p: p.__setitem__("open_questions", [1]),
    ],
)
def test_agent_result_contract_violation_maps_to_model_invalid_output(mutator) -> None:
    payload = valid_payload()
    mutator(payload)
    assert_invalid(
        json.dumps(payload, separators=(",", ":")),
        ModelOutputReason.CONTRACT_VIOLATION,
    )


def test_raw_model_content_is_not_stored_in_failure_details() -> None:
    secret = "DO_NOT_STORE_THIS_RAW_MODEL_TEXT"
    error = assert_invalid(secret, ModelOutputReason.INVALID_JSON)
    encoded = json.dumps(error.failure.to_dict())
    assert secret not in encoded


def test_oversized_content_is_rejected_before_json_decode() -> None:
    content = "x" * (MAX_MODEL_OUTPUT_CONTENT_BYTES + 1)
    error = assert_invalid(content, ModelOutputReason.CONTENT_TOO_LARGE)
    assert error.failure.to_dict()["details"]["content_bytes"] == (
        MAX_MODEL_OUTPUT_CONTENT_BYTES + 1
    )


def test_exact_content_bound_reaches_json_stage() -> None:
    content = "x" * MAX_MODEL_OUTPUT_CONTENT_BYTES
    error = assert_invalid(content, ModelOutputReason.INVALID_JSON)
    assert error.failure.to_dict()["details"]["content_bytes"] == (
        MAX_MODEL_OUTPUT_CONTENT_BYTES
    )


def test_wrong_input_type_is_programmer_error_not_model_failure() -> None:
    with pytest.raises(TypeError, match="GenerateResponse"):
        parse_agent_result_response({"content": "{}"})  # type: ignore[arg-type]


def test_model_output_error_does_not_authorize_retry_or_state_transition() -> None:
    error = assert_invalid("not-json", ModelOutputReason.INVALID_JSON)
    assert not hasattr(error, "retry")
    assert not hasattr(error, "retryable")
    assert not hasattr(error, "retry_count")
    assert not hasattr(error, "next_state")


def test_parser_has_no_project_state_or_uav_side_effect_imports() -> None:
    import ai.providers.model_output as module

    text = open(module.__file__, encoding="utf-8").read().lower()
    for forbidden in (
        "sqlite3",
        "project_state",
        "mavsdk",
        "px4",
        "gazebo",
        "requests",
        "httpx",
    ):
        assert forbidden not in text


def test_parser_has_no_retry_loop() -> None:
    import ai.providers.model_output as module

    text = open(module.__file__, encoding="utf-8").read()
    assert "while True" not in text
    assert "time.sleep" not in text
    assert "asyncio.sleep" not in text
