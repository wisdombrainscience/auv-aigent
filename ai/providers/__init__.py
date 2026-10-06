"""Local-only AI provider abstractions."""

from ai.providers.base import (
    LOCAL_AI_PROVIDER_INTERFACE_VERSION,
    LocalAIProvider,
)
from ai.providers.contracts import (
    PROVIDER_WIRE_CONTRACT_VERSION,
    GenerateRequest,
    GenerateResponse,
    ProviderContractError,
    ProviderHealth,
    ProviderModel,
    ProviderModelList,
)
from ai.providers.errors import (
    PROVIDER_ERROR_CONTRACT_VERSION,
    ProviderFailureCode,
    ProviderRuntimeError,
    build_provider_failure,
)

from ai.providers.model_output import (
    MAX_MODEL_OUTPUT_CONTENT_BYTES,
    MODEL_OUTPUT_CONTRACT_VERSION,
    ModelOutputError,
    ModelOutputReason,
    parse_agent_result_response,
)
from ai.providers.mock import (
    MOCK_PROVIDER_CONTRACT_VERSION,
    MockProvider,
    MockProviderScriptExhausted,
)
from ai.providers.retry import (
    DEFAULT_PROVIDER_MAX_ATTEMPTS,
    MAX_PROVIDER_MAX_ATTEMPTS,
    PROVIDER_RETRY_CONTRACT_VERSION,
    ProviderRetryExhausted,
    ProviderRetryPolicy,
    RetryingLocalAIProvider,
    is_transient_provider_failure,
)
from ai.providers.ollama import (
    DEFAULT_OLLAMA_ENDPOINT,
    DEFAULT_OLLAMA_HEALTH_TIMEOUT_S,
    MAX_OLLAMA_HEALTH_RESPONSE_BYTES,
    MAX_OLLAMA_MODEL_LIST_RESPONSE_BYTES,
    DEFAULT_OLLAMA_GENERATE_TIMEOUT_S,
    MAX_OLLAMA_GENERATE_REQUEST_BYTES,
    MAX_OLLAMA_GENERATE_RESPONSE_BYTES,
    OLLAMA_PROVIDER_CONTRACT_VERSION,
    OllamaModelListError,
    OllamaGenerateError,
    OllamaProvider,
    OllamaProviderConfigurationError,
)

__all__ = [
    "DEFAULT_PROVIDER_MAX_ATTEMPTS",
    "MAX_PROVIDER_MAX_ATTEMPTS",
    "PROVIDER_RETRY_CONTRACT_VERSION",
    "ProviderRetryExhausted",
    "ProviderRetryPolicy",
    "RetryingLocalAIProvider",
    "is_transient_provider_failure",
    "MAX_MODEL_OUTPUT_CONTENT_BYTES",
    "MODEL_OUTPUT_CONTRACT_VERSION",
    "ModelOutputError",
    "ModelOutputReason",
    "parse_agent_result_response",
    "PROVIDER_ERROR_CONTRACT_VERSION",
    "ProviderFailureCode",
    "ProviderRuntimeError",
    "build_provider_failure",
    "LOCAL_AI_PROVIDER_INTERFACE_VERSION",
    "LocalAIProvider",
    "PROVIDER_WIRE_CONTRACT_VERSION",
    "GenerateRequest",
    "GenerateResponse",
    "ProviderContractError",
    "ProviderHealth",
    "ProviderModel",
    "ProviderModelList",
    "MOCK_PROVIDER_CONTRACT_VERSION",
    "MockProvider",
    "MockProviderScriptExhausted",
    "DEFAULT_OLLAMA_ENDPOINT",
    "DEFAULT_OLLAMA_HEALTH_TIMEOUT_S",
    "MAX_OLLAMA_HEALTH_RESPONSE_BYTES",
    "MAX_OLLAMA_MODEL_LIST_RESPONSE_BYTES",
    "DEFAULT_OLLAMA_GENERATE_TIMEOUT_S",
    "MAX_OLLAMA_GENERATE_REQUEST_BYTES",
    "MAX_OLLAMA_GENERATE_RESPONSE_BYTES",
    "OLLAMA_PROVIDER_CONTRACT_VERSION",
    "OllamaModelListError",
    "OllamaGenerateError",
    "OllamaProvider",
    "OllamaProviderConfigurationError",
]
