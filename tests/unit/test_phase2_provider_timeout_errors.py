from __future__ import annotations

import socket
from urllib.error import URLError

import pytest

from ai.contracts.failures import FailureDescriptor
from ai.providers.contracts import GenerateRequest
from ai.providers.errors import (
    PROVIDER_ERROR_CONTRACT_VERSION,
    ProviderFailureCode,
    ProviderRuntimeError,
    build_provider_failure,
)
from ai.providers.ollama import (
    OllamaGenerateError,
    OllamaModelListError,
    OllamaProvider,
)


class RaisingOpener:
    def __init__(self, exc: BaseException):
        self.exc = exc
        self.calls = 0

    def open(self, *args, **kwargs):
        self.calls += 1
        raise self.exc


def generate_request() -> GenerateRequest:
    return GenerateRequest(
        model="m1",
        prompt="test",
        system_prompt=None,
        output_schema=None,
    )


def assert_provider_failure(
    exc: ProviderRuntimeError,
    *,
    code: str,
    operation: str,
) -> None:
    assert isinstance(exc.failure, FailureDescriptor)
    payload = exc.failure.to_dict()
    assert payload["class"] == "PROVIDER"
    assert payload["code"] == code
    assert payload["details"]["provider"] == "ollama"
    assert payload["details"]["operation"] == operation
    assert "retry" not in payload
    assert "retry_count" not in payload
    assert "next_state" not in payload


def test_provider_error_contract_version_is_explicit() -> None:
    assert PROVIDER_ERROR_CONTRACT_VERSION == "1.0"


def test_provider_failure_codes_are_exact() -> None:
    assert {item.value for item in ProviderFailureCode} == {
        "PROVIDER_TIMEOUT",
        "PROVIDER_UNAVAILABLE",
        "PROVIDER_HTTP_ERROR",
        "PROVIDER_REQUEST_TOO_LARGE",
        "PROVIDER_RESPONSE_TOO_LARGE",
        "PROVIDER_INVALID_RESPONSE",
        "PROVIDER_MODEL_MISMATCH",
        "PROVIDER_INCOMPLETE_RESPONSE",
    }


def test_builder_produces_frozen_c1_8_provider_descriptor() -> None:
    failure = build_provider_failure(
        provider="ollama",
        operation="generate",
        code=ProviderFailureCode.TIMEOUT,
        message="Local provider timed out.",
        details={"timeout_s": 2.0},
    )
    assert failure.to_dict() == {
        "class": "PROVIDER",
        "code": "PROVIDER_TIMEOUT",
        "message": "Local provider timed out.",
        "details": {
            "provider": "ollama",
            "operation": "generate",
            "timeout_s": 2.0,
        },
    }


@pytest.mark.parametrize(
    "exc",
    [
        socket.timeout("timed out"),
        TimeoutError("timed out"),
        URLError(socket.timeout("timed out")),
        URLError(TimeoutError("timed out")),
    ],
)
def test_generate_timeout_maps_distinctly(exc: BaseException) -> None:
    provider = OllamaProvider()
    opener = RaisingOpener(exc)
    provider._opener = opener

    with pytest.raises(OllamaGenerateError) as caught:
        provider.generate(generate_request())

    assert str(caught.value) == "timeout"
    assert opener.calls == 1
    assert_provider_failure(
        caught.value,
        code="PROVIDER_TIMEOUT",
        operation="generate",
    )
    assert caught.value.failure.to_dict()["details"]["timeout_s"] == 60.0


def test_generate_unavailable_is_not_misclassified_as_timeout() -> None:
    provider = OllamaProvider()
    opener = RaisingOpener(URLError(ConnectionRefusedError("refused")))
    provider._opener = opener

    with pytest.raises(OllamaGenerateError) as caught:
        provider.generate(generate_request())

    assert str(caught.value) == "unavailable"
    assert opener.calls == 1
    assert_provider_failure(
        caught.value,
        code="PROVIDER_UNAVAILABLE",
        operation="generate",
    )


def test_model_listing_timeout_maps_to_provider_timeout() -> None:
    provider = OllamaProvider(health_timeout_s=0.2)
    opener = RaisingOpener(URLError(socket.timeout("timed out")))
    provider._opener = opener

    with pytest.raises(OllamaModelListError) as caught:
        provider.list_models()

    assert str(caught.value) == "timeout"
    assert opener.calls == 1
    assert_provider_failure(
        caught.value,
        code="PROVIDER_TIMEOUT",
        operation="list_models",
    )
    assert caught.value.failure.to_dict()["details"]["timeout_s"] == 0.2


def test_model_listing_unavailable_maps_separately() -> None:
    provider = OllamaProvider()
    opener = RaisingOpener(URLError(ConnectionRefusedError("refused")))
    provider._opener = opener

    with pytest.raises(OllamaModelListError) as caught:
        provider.list_models()

    assert str(caught.value) == "unavailable"
    assert opener.calls == 1
    assert_provider_failure(
        caught.value,
        code="PROVIDER_UNAVAILABLE",
        operation="list_models",
    )


def test_health_timeout_is_distinguished_without_raising() -> None:
    provider = OllamaProvider()
    opener = RaisingOpener(URLError(socket.timeout("timed out")))
    provider._opener = opener

    health = provider.health()

    assert opener.calls == 1
    assert health.ok is False
    assert health.provider == "ollama"
    assert health.detail == "timeout"


def test_health_unavailable_remains_distinct() -> None:
    provider = OllamaProvider()
    opener = RaisingOpener(URLError(ConnectionRefusedError("refused")))
    provider._opener = opener

    health = provider.health()

    assert opener.calls == 1
    assert health.ok is False
    assert health.detail == "unavailable"


def test_operational_exception_does_not_authorize_retry() -> None:
    provider = OllamaProvider()
    opener = RaisingOpener(socket.timeout("timed out"))
    provider._opener = opener

    with pytest.raises(OllamaGenerateError) as caught:
        provider.generate(generate_request())

    error = caught.value
    assert not hasattr(error, "retry")
    assert not hasattr(error, "retryable")
    assert not hasattr(error, "retry_count")
    assert not hasattr(error, "next_state")
    assert opener.calls == 1


def test_provider_error_module_contains_no_retry_loop() -> None:
    import ai.providers.errors as module

    text = open(module.__file__, encoding="utf-8").read()
    assert "while True" not in text
    assert "time.sleep" not in text
    assert "asyncio.sleep" not in text


def test_ollama_provider_still_contains_no_retry_loop() -> None:
    import ai.providers.ollama as module

    text = open(module.__file__, encoding="utf-8").read()
    assert "while True" not in text
    assert "time.sleep" not in text
    assert "asyncio.sleep" not in text
