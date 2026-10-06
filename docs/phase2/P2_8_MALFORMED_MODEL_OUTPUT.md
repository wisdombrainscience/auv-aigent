# P2.8 — Malformed Model Output Handling

Status: candidate for target gate.

## Purpose

P2.8 implements the frozen architecture rule:

```text
Malformed model output -> MODEL_INVALID_OUTPUT
```

Raw `GenerateResponse.content` is never accepted as Project State. It must pass
an explicit structured-output boundary before it can become an `AgentResult`.

The boundary is:

```text
GenerateResponse.content
        -> bounded strict JSON decode
        -> duplicate-key rejection
        -> top-level object check
        -> C1.5 AgentResult validation
        -> AgentResult
```

Any model-content failure produces a C1.8 `FailureDescriptor` with:

```text
class = MODEL
code  = MODEL_INVALID_OUTPUT
```

## Failure reasons

The single architectural failure code is retained, while deterministic reason
metadata distinguishes:

```text
CONTENT_TOO_LARGE
INVALID_JSON
DUPLICATE_JSON_KEY
TOP_LEVEL_NOT_OBJECT
CONTRACT_VIOLATION
```

Raw model content is not copied into failure details. Only model identity,
contract name, reason, and byte count are retained.

## Strictness

P2.8 rejects:

- prose or markdown fences around JSON;
- trailing non-JSON text;
- duplicate JSON keys, including nested duplicates;
- NaN/Infinity and overflow to non-finite floats;
- non-object top-level values;
- missing/extra AgentResult fields;
- invalid AgentResult status/provenance/types.

The content parser has an 8 MiB hard bound, matching the existing bounded
provider generation response scale.

## Authority boundary

P2.8 only returns a validated `AgentResult` or raises `ModelOutputError`.
It does not:

- write Project State;
- approve an artifact;
- transition a task;
- retry generation;
- run reviewer/validator logic;
- implement an Agent Engine or Orchestrator.

P2.9 remains responsible for bounded provider retry policy.
