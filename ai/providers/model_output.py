from __future__ import annotations

from enum import Enum
import json
import math
from typing import Any

from ai.contracts.agent_result import (
    AgentResult,
    AgentResultError,
)
from ai.contracts.failures import FailureDescriptor
from ai.providers.contracts import GenerateResponse


MODEL_OUTPUT_CONTRACT_VERSION = "1.0"
MAX_MODEL_OUTPUT_CONTENT_BYTES = 8 * 1024 * 1024


class ModelOutputReason(str, Enum):
    CONTENT_TOO_LARGE = "CONTENT_TOO_LARGE"
    INVALID_JSON = "INVALID_JSON"
    DUPLICATE_JSON_KEY = "DUPLICATE_JSON_KEY"
    TOP_LEVEL_NOT_OBJECT = "TOP_LEVEL_NOT_OBJECT"
    CONTRACT_VIOLATION = "CONTRACT_VIOLATION"


class ModelOutputError(ValueError):
    """Malformed structured model output carrying C1.8 failure evidence.

    This exception describes invalid model content only. It does not retry,
    mutate Project State, approve artifacts, or choose a task transition.
    """

    def __init__(
        self,
        reason: ModelOutputReason,
        failure: FailureDescriptor,
    ) -> None:
        if not isinstance(reason, ModelOutputReason):
            raise TypeError("reason must be ModelOutputReason")
        if not isinstance(failure, FailureDescriptor):
            raise TypeError("failure must be FailureDescriptor")
        if failure.failure_class.value != "MODEL":
            raise ValueError("model output errors require MODEL failure class")
        if failure.code != "MODEL_INVALID_OUTPUT":
            raise ValueError("model output errors require MODEL_INVALID_OUTPUT")

        self.reason = reason
        self.failure = failure
        super().__init__(reason.value)


class _DuplicateJSONKey(ValueError):
    def __init__(self, key: str) -> None:
        self.key = key
        super().__init__(key)


def _reject_duplicate_keys(
    pairs: list[tuple[str, Any]],
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateJSONKey(key)
        result[key] = value
    return result


def _reject_nonstandard_constant(value: str) -> None:
    raise ValueError(f"non-standard JSON constant: {value}")


def _reject_nonfinite_numbers(value: Any) -> None:
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("non-finite JSON number")
        return
    if isinstance(value, list):
        for item in value:
            _reject_nonfinite_numbers(item)
        return
    if isinstance(value, dict):
        for item in value.values():
            _reject_nonfinite_numbers(item)


def _build_invalid_output_failure(
    *,
    response: GenerateResponse,
    reason: ModelOutputReason,
    content_bytes: int,
) -> FailureDescriptor:
    return FailureDescriptor.from_dict(
        {
            "class": "MODEL",
            "code": "MODEL_INVALID_OUTPUT",
            "message": "Model output did not satisfy the AgentResult contract.",
            "details": {
                "contract": "AgentResult",
                "model": response.model,
                "reason": reason.value,
                "content_bytes": content_bytes,
            },
        }
    )


def _raise_invalid_output(
    *,
    response: GenerateResponse,
    reason: ModelOutputReason,
    content_bytes: int,
) -> None:
    raise ModelOutputError(
        reason,
        _build_invalid_output_failure(
            response=response,
            reason=reason,
            content_bytes=content_bytes,
        ),
    )


def parse_agent_result_response(
    response: GenerateResponse,
) -> AgentResult:
    """Convert one provider-neutral response into a validated AgentResult.

    The raw model text is never returned as structured state. It must pass:

    1. the bounded content-size gate;
    2. strict JSON decoding with duplicate-key rejection;
    3. top-level object validation;
    4. the frozen C1.5 AgentResult contract.

    Any malformed model content fails as `MODEL_INVALID_OUTPUT`.
    """

    if not isinstance(response, GenerateResponse):
        raise TypeError("response must be GenerateResponse")

    content_bytes = len(response.content.encode("utf-8"))
    if content_bytes > MAX_MODEL_OUTPUT_CONTENT_BYTES:
        _raise_invalid_output(
            response=response,
            reason=ModelOutputReason.CONTENT_TOO_LARGE,
            content_bytes=content_bytes,
        )

    try:
        decoded = json.loads(
            response.content,
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_nonstandard_constant,
        )
        _reject_nonfinite_numbers(decoded)
    except _DuplicateJSONKey:
        _raise_invalid_output(
            response=response,
            reason=ModelOutputReason.DUPLICATE_JSON_KEY,
            content_bytes=content_bytes,
        )
    except (json.JSONDecodeError, UnicodeError, ValueError, TypeError):
        _raise_invalid_output(
            response=response,
            reason=ModelOutputReason.INVALID_JSON,
            content_bytes=content_bytes,
        )

    if not isinstance(decoded, dict):
        _raise_invalid_output(
            response=response,
            reason=ModelOutputReason.TOP_LEVEL_NOT_OBJECT,
            content_bytes=content_bytes,
        )

    try:
        return AgentResult.from_dict(decoded)
    except AgentResultError:
        _raise_invalid_output(
            response=response,
            reason=ModelOutputReason.CONTRACT_VIOLATION,
            content_bytes=content_bytes,
        )
