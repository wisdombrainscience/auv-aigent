from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, TypeVar

from ai.providers.base import LocalAIProvider
from ai.providers.contracts import GenerateRequest, GenerateResponse, ProviderHealth, ProviderModelList
from ai.providers.errors import ProviderFailureCode, ProviderRuntimeError

PROVIDER_RETRY_CONTRACT_VERSION = "1.0"
DEFAULT_PROVIDER_MAX_ATTEMPTS = 2
MAX_PROVIDER_MAX_ATTEMPTS = 2

_TRANSIENT_CODES = frozenset({
    ProviderFailureCode.TIMEOUT.value,
    ProviderFailureCode.UNAVAILABLE.value,
})
_TRANSIENT_HTTP_STATUSES = frozenset({429, 502, 503, 504})


@dataclass(frozen=True)
class ProviderRetryPolicy:
    max_attempts: int = DEFAULT_PROVIDER_MAX_ATTEMPTS

    def __post_init__(self) -> None:
        if isinstance(self.max_attempts, bool) or not isinstance(self.max_attempts, int):
            raise ValueError("max_attempts must be an integer")
        if not 1 <= self.max_attempts <= MAX_PROVIDER_MAX_ATTEMPTS:
            raise ValueError("max_attempts must be between 1 and 2")


class ProviderRetryExhausted(ProviderRuntimeError):
    def __init__(self, *, attempts_used: int, max_attempts: int, last_error: ProviderRuntimeError) -> None:
        if not isinstance(last_error, ProviderRuntimeError):
            raise TypeError("last_error must be ProviderRuntimeError")
        if not isinstance(attempts_used, int) or attempts_used < 1:
            raise ValueError("attempts_used must be >= 1")
        if not isinstance(max_attempts, int) or max_attempts < 1:
            raise ValueError("max_attempts must be >= 1")
        if attempts_used > max_attempts:
            raise ValueError("attempts_used cannot exceed max_attempts")
        self.attempts_used = attempts_used
        self.max_attempts = max_attempts
        self.last_error = last_error
        super().__init__(last_error.reason, last_error.failure)


def is_transient_provider_failure(error: ProviderRuntimeError) -> bool:
    if not isinstance(error, ProviderRuntimeError):
        raise TypeError("error must be ProviderRuntimeError")
    failure = error.failure
    if failure.code in _TRANSIENT_CODES:
        return True
    if failure.code != ProviderFailureCode.HTTP_ERROR.value:
        return False
    status = failure.to_dict()["details"].get("http_status")
    return isinstance(status, int) and not isinstance(status, bool) and status in _TRANSIENT_HTTP_STATUSES


T = TypeVar("T")


def _call_with_retry(operation: Callable[[], T], *, policy: ProviderRetryPolicy) -> T:
    if not callable(operation):
        raise TypeError("operation must be callable")
    if not isinstance(policy, ProviderRetryPolicy):
        raise TypeError("policy must be ProviderRetryPolicy")
    attempts = 0
    while attempts < policy.max_attempts:
        attempts += 1
        try:
            return operation()
        except ProviderRuntimeError as exc:
            if not is_transient_provider_failure(exc):
                raise
            if attempts >= policy.max_attempts:
                raise ProviderRetryExhausted(
                    attempts_used=attempts,
                    max_attempts=policy.max_attempts,
                    last_error=exc,
                ) from exc
    raise RuntimeError("provider retry loop terminated unexpectedly")


class RetryingLocalAIProvider:
    def __init__(self, provider: LocalAIProvider, *, policy: ProviderRetryPolicy | None = None) -> None:
        if not isinstance(provider, LocalAIProvider):
            raise TypeError("provider must satisfy LocalAIProvider")
        if policy is None:
            policy = ProviderRetryPolicy()
        if not isinstance(policy, ProviderRetryPolicy):
            raise TypeError("policy must be ProviderRetryPolicy")
        self._provider = provider
        self._policy = policy

    @property
    def policy(self) -> ProviderRetryPolicy:
        return self._policy

    def health(self) -> ProviderHealth:
        return self._provider.health()

    def list_models(self) -> ProviderModelList:
        return _call_with_retry(self._provider.list_models, policy=self._policy)

    def generate(self, request: GenerateRequest) -> GenerateResponse:
        if not isinstance(request, GenerateRequest):
            raise TypeError("request must be GenerateRequest")
        return _call_with_retry(lambda: self._provider.generate(request), policy=self._policy)
