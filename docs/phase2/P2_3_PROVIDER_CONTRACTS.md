# P2.3 — Provider Request/Response Contracts

Status: candidate for target gate.

## Purpose

P2.3 freezes provider-neutral wire contracts before any Ollama transport is
implemented.

The contracts are:

```text
ProviderHealth
ProviderModel
ProviderModelList
GenerateRequest
GenerateResponse
```

`LocalAIProvider` is updated from the temporary P2.1 opaque `Any` boundary to:

```text
health() -> ProviderHealth
list_models() -> ProviderModelList
generate(GenerateRequest) -> GenerateResponse
```

MockProvider is updated to use the same typed contracts.

## GenerateRequest

Frozen fields:

```json
{
  "model": "local-model:7b",
  "prompt": "Return a structured engineering proposal.",
  "system_prompt": "You are the requirements engineer.",
  "output_schema": {}
}
```

`system_prompt` and `output_schema` may be null.

The future Model Manager chooses the model. P2.3 does not move model-selection
policy into the provider.

Sampling parameters, inference queueing, keep-alive, load/unload policy, and
retry policy are intentionally absent.

## GenerateResponse

Frozen fields:

```json
{
  "model": "local-model:7b",
  "content": "{"status":"PROPOSED"}",
  "done": true
}
```

The provider abstraction is synchronous and complete: `done` must be true.

`content` remains raw model text. P2.3 does not parse it into AgentResult and
does not permit raw model text to enter Project State. Malformed structured
model output is handled later under the dedicated malformed-output work package
and the existing `MODEL_INVALID_OUTPUT` failure taxonomy.

## Health and model listing

Health is only provider availability:

```json
{
  "ok": true,
  "provider": "ollama",
  "detail": null
}
```

It is not a UAV health verdict.

Model listing intentionally freezes only local model identity:

```json
{
  "models": [
    {"name": "local-model:7b"}
  ]
}
```

P2.5 will map Ollama's model-list response into this contract.

## Strictness

Unknown top-level fields are rejected. Strings are not silently trimmed.
Schemas use immutable strict-JSON snapshots and reject NaN/Infinity.

## Deliberate boundaries

P2.3 still introduces no:

- Ollama HTTP call;
- timeout handling;
- malformed model-output parsing;
- provider retry;
- Model Manager;
- Agent Engine;
- Project State;
- Orchestrator;
- RAG;
- UAV Bridge.
