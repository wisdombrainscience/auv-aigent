from __future__ import annotations

from typing import Protocol, runtime_checkable

from ai.providers.contracts import (
    GenerateRequest,
    GenerateResponse,
    ProviderHealth,
    ProviderModelList,
)


LOCAL_AI_PROVIDER_INTERFACE_VERSION = "1.1"


@runtime_checkable
class LocalAIProvider(Protocol):
    """Structural interface for local-only AI provider implementations.

    P2.3 binds the P2.1 provider operations to provider-neutral, immutable
    request/response contracts.

    This interface owns no project state, task state, scheduling, agent roles,
    artifact approval, retry loop, model-manager policy, or UAV execution.
    """

    def health(self) -> ProviderHealth:
        ...

    def list_models(self) -> ProviderModelList:
        ...

    def generate(self, request: GenerateRequest) -> GenerateResponse:
        ...
