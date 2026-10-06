from __future__ import annotations

from enum import Enum
from typing import Any, Mapping

from ai.contracts.failures import FailureDescriptor


PROVIDER_ERROR_CONTRACT_VERSION = "1.0"


class ProviderFailureCode(str, Enum):
    """Stable provider-operational failure codes for Phase 2."""

    TIMEOUT = "PROVIDER_TIMEOUT"
    UNAVAILABLE = "PROVIDER_UNAVAILABLE"
    HTTP_ERROR = "PROVIDER_HTTP_ERROR"
    REQUEST_TOO_LARGE = "PROVIDER_REQUEST_TOO_LARGE"
    RESPONSE_TOO_LARGE = "PROVIDER_RESPONSE_TOO_LARGE"
    INVALID_RESPONSE = "PROVIDER_INVALID_RESPONSE"
    MODEL_MISMATCH = "PROVIDER_MODEL_MISMATCH"
    INCOMPLETE_RESPONSE = "PROVIDER_INCOMPLETE_RESPONSE"


class ProviderRuntimeError(RuntimeError):
    """Provider operation failure carrying the frozen C1.8 descriptor.

    This exception describes what happened. It does not authorize retry,
    increment attempt counters, or choose a task transition.
    """

    def __init__(
        self,
        reason: str,
        failure: FailureDescriptor,
    ) -> None:
        if not isinstance(reason, str) or not reason or reason != reason.strip():
            raise ValueError("reason must be clean non-empty text")
        if not isinstance(failure, FailureDescriptor):
            raise TypeError("failure must be FailureDescriptor")
        if failure.failure_class.value != "PROVIDER":
            raise ValueError("provider runtime errors require PROVIDER failure class")

        self.reason = reason
        self.failure = failure
        super().__init__(reason)


def build_provider_failure(
    *,
    provider: str,
    operation: str,
    code: ProviderFailureCode,
    message: str,
    details: Mapping[str, Any] | None = None,
) -> FailureDescriptor:
    """Build one immutable provider FailureDescriptor.

    The C1.8 contract requires category-prefixed failure codes, so the Phase-2
    implementation represents provider timeout as `PROVIDER_TIMEOUT`.
    """

    if not isinstance(provider, str) or not provider or provider != provider.strip():
        raise ValueError("provider must be clean non-empty text")
    if not isinstance(operation, str) or not operation or operation != operation.strip():
        raise ValueError("operation must be clean non-empty text")
    if not isinstance(code, ProviderFailureCode):
        raise TypeError("code must be ProviderFailureCode")
    if not isinstance(message, str) or not message or message != message.strip():
        raise ValueError("message must be clean non-empty text")

    merged: dict[str, Any] = {
        "provider": provider,
        "operation": operation,
    }
    if details is not None:
        if not isinstance(details, Mapping):
            raise TypeError("details must be a mapping or None")
        for key, value in details.items():
            if key in merged:
                raise ValueError(f"reserved provider failure detail key: {key}")
            merged[key] = value

    return FailureDescriptor.from_dict(
        {
            "class": "PROVIDER",
            "code": code.value,
            "message": message,
            "details": merged,
        }
    )
