from __future__ import annotations

from typing import Sequence

from ai.providers.base import LocalAIProvider
from ai.providers.contracts import (
    GenerateRequest,
    GenerateResponse,
    ProviderHealth,
    ProviderModelList,
)


MOCK_PROVIDER_CONTRACT_VERSION = "1.1"


class MockProviderScriptExhausted(RuntimeError):
    """Raised when a finite mock generation script has been consumed."""


class MockProvider:
    """Deterministic, typed, network-free LocalAIProvider for tests."""

    def __init__(
        self,
        *,
        health_response: ProviderHealth | None = None,
        models_response: ProviderModelList | None = None,
        generate_responses: Sequence[GenerateResponse] = (),
    ) -> None:
        if isinstance(generate_responses, (str, bytes, bytearray)):
            raise TypeError(
                "generate_responses must be a finite sequence of "
                "GenerateResponse values"
            )
        if not isinstance(generate_responses, Sequence):
            raise TypeError("generate_responses must be a finite sequence")

        if health_response is None:
            health_response = ProviderHealth(
                ok=True,
                provider="mock",
                detail=None,
            )
        if not isinstance(health_response, ProviderHealth):
            raise TypeError("health_response must be ProviderHealth")

        if models_response is None:
            models_response = ProviderModelList(models=())
        if not isinstance(models_response, ProviderModelList):
            raise TypeError("models_response must be ProviderModelList")

        responses = tuple(generate_responses)
        if not all(
            isinstance(item, GenerateResponse)
            for item in responses
        ):
            raise TypeError(
                "all generate_responses must be GenerateResponse"
            )

        self._health_response = health_response
        self._models_response = models_response
        self._generate_responses = responses
        self._generate_cursor = 0

    def health(self) -> ProviderHealth:
        return self._health_response

    def list_models(self) -> ProviderModelList:
        return self._models_response

    def generate(
        self,
        request: GenerateRequest,
    ) -> GenerateResponse:
        if not isinstance(request, GenerateRequest):
            raise TypeError("request must be GenerateRequest")

        if self._generate_cursor >= len(self._generate_responses):
            raise MockProviderScriptExhausted(
                "mock generate script exhausted"
            )

        response = self._generate_responses[self._generate_cursor]
        self._generate_cursor += 1

        # A provider must not silently report a different model than the one
        # requested. This catches bad fixtures before later Agent Engine tests.
        if response.model != request.model:
            raise ValueError(
                "mock response model does not match request model"
            )

        return response

    @property
    def generate_call_count(self) -> int:
        return self._generate_cursor

    @property
    def remaining_generate_responses(self) -> int:
        return len(self._generate_responses) - self._generate_cursor


assert isinstance(MockProvider(), LocalAIProvider)
