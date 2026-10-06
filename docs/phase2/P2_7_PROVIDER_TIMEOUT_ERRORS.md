# P2.7 — Provider Timeout and Operational Error Handling

Status: candidate for target gate.

## Purpose

P2.7 separates provider timeout from generic provider unavailability and maps
provider operational failures into the frozen C1.8 `FailureDescriptor`.

The C1.8 contract requires category-prefixed codes. Therefore the architecture's
provider-timeout concept is represented by:

```text
PROVIDER_TIMEOUT
```

rather than introducing a non-prefixed failure code.

## Provider failure codes

P2.7 freezes the Phase-2 operational codes:

```text
PROVIDER_TIMEOUT
PROVIDER_UNAVAILABLE
PROVIDER_HTTP_ERROR
PROVIDER_REQUEST_TOO_LARGE
PROVIDER_RESPONSE_TOO_LARGE
PROVIDER_INVALID_RESPONSE
PROVIDER_MODEL_MISMATCH
PROVIDER_INCOMPLETE_RESPONSE
```

All use failure class `PROVIDER`.

## Exception contract

`OllamaModelListError` and `OllamaGenerateError` now carry:

```text
error.reason
error.failure -> FailureDescriptor
```

The historical short reason strings such as `http_status=503`, `unavailable`,
and `invalid_response` remain available for compatibility.

Example:

```json
{
  "class": "PROVIDER",
  "code": "PROVIDER_TIMEOUT",
  "message": "Local Ollama generation failed.",
  "details": {
    "provider": "ollama",
    "operation": "generate",
    "timeout_s": 60.0
  }
}
```

## Timeout distinction

Timeouts include direct `TimeoutError` / `socket.timeout` and timeout reasons
wrapped by `URLError`.

They are no longer collapsed into `unavailable`.

Health remains non-throwing by the frozen provider contract:

```text
ProviderHealth(ok=false, detail="timeout")
```

versus:

```text
ProviderHealth(ok=false, detail="unavailable")
```

## Critical anti-loop boundary

P2.7 does not retry.

The structured failure contains no retry flag, retry count, next state, or
automatic action. P2.9 owns bounded provider retry policy.

P2.8 remains responsible for invalid structured **model content**
(`MODEL_INVALID_OUTPUT`). P2.7 `PROVIDER_INVALID_RESPONSE` refers only to the
provider HTTP/protocol envelope.

No Project State, Agent Engine, Orchestrator, Model Manager, RAG, UAV Bridge,
or UAV execution-plane change is introduced.
