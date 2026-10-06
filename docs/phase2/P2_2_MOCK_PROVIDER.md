# P2.2 — MockProvider

Status: candidate for target gate.

## Purpose

P2.2 adds the deterministic network-free provider required by the frozen
architecture for repeatable tests.

`MockProvider` implements the P2.1 `LocalAIProvider` interface:

```text
health()
list_models()
generate()
```

## Deterministic finite script

Generation responses are supplied at construction as a finite sequence and are
consumed in order.

Example conceptually:

```text
response[0] -> first generate()
response[1] -> second generate()
...
exhausted   -> MockProviderScriptExhausted
```

The provider never repeats the final response automatically and does not
implement retry logic.

The script and configured health/model values are deep-copied at construction,
and returned mutable values are copied again. Test code therefore cannot
silently mutate the provider's internal fixtures.

## Memory boundary

MockProvider does not retain generation requests or append to a call-history
collection. It keeps only:

- the finite construction-time response tuple;
- a bounded cursor into that tuple.

This prevents test request history from growing without bound.

## P2.2 non-goals

P2.2 deliberately does not introduce:

- provider request/response schemas — P2.3;
- Ollama transport;
- HTTP/network access;
- provider timeout logic;
- malformed-JSON handling;
- provider retry;
- Model Manager;
- Project State;
- Agent Engine;
- Orchestrator;
- RAG;
- UAV Bridge.

The opaque provider values remain temporary until P2.3 freezes the wire
contracts.
