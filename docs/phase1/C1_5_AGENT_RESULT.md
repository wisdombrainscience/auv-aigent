# C1.5 — AgentResult Contract

Status: candidate for target gate.

## Frozen envelope

C1.5 implements the minimum structured agent result from the architecture
baseline:

```json
{
  "status": "PROPOSED",
  "claims": [],
  "assumptions": [],
  "calculations": [],
  "artifacts": [],
  "risks": [],
  "missing_information": [],
  "open_questions": []
}
```

All eight fields are required. Unknown top-level fields are rejected.

## Authority rule

A valid `AgentResult` is still only a proposal.

C1.5 therefore accepts only:

`status = PROPOSED`

An agent cannot emit `APPROVED`, `DONE`, `PASS`, or another value and thereby
promote its own output into project truth.

Claims must be objects carrying a valid C1.4 provenance `classification`.
This ensures, for example, that `USER_FACT`, `AI_PROPOSAL`,
`SIMULATION_RESULT`, and `VERIFIER_RESULT` remain distinguishable.

## Strict transport boundary

The contract rejects:

- malformed/missing top-level fields;
- unknown top-level fields;
- invalid provenance classifications in claims;
- NaN and positive/negative Infinity;
- non-JSON nested objects;
- non-string object keys;
- malformed human-facing missing-information/open-question strings.

Parsed nested data is frozen into an immutable snapshot, and serialization
returns detached standard JSON containers.

## Deliberate boundaries

C1.5 does not yet freeze the internal schema for individual calculations,
artifact payloads, or risks. Those lists are structured JSON containers so
later dedicated contracts can be introduced without silently inventing fields
now.

C1.5 also does not implement provider JSON parsing, retry behavior,
`MODEL_INVALID_OUTPUT` failure mapping, persistence, Agent Engine, Ollama,
review, deterministic validation, orchestration, RAG, or UAV execution.

Those remain later gated work packages.

## UAV trust boundary

No existing UAV Runtime Core file is modified. `AgentResult` cannot command
PX4/MAVSDK and cannot create an authoritative simulation verdict.
