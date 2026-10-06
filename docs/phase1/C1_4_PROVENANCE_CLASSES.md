# C1.4 — Provenance Classes

Status: candidate for target gate.

## Scope

C1.4 freezes the provenance vocabulary used later by requirements, claims,
calculations, artifacts, simulation evidence, and verifier evidence.

The canonical classes are exactly:

- `USER_FACT`
- `VERIFIED_FACT`
- `USER_REQUIREMENT`
- `ENGINEERING_ASSUMPTION`
- `AI_PROPOSAL`
- `CALCULATED_VALUE`
- `SIMULATION_RESULT`
- `VERIFIER_RESULT`

## Semantic boundary

Classification records where a claim/value came from or what kind of
engineering statement it is. It does not automatically make the claim true.

Important distinctions:

- `USER_FACT` is not silently promoted to `VERIFIED_FACT`.
- `USER_REQUIREMENT` is not treated as a factual observation.
- `ENGINEERING_ASSUMPTION` remains explicitly distinguishable from facts.
- `AI_PROPOSAL` remains advisory.
- `CALCULATED_VALUE` is distinct from independently verified fact.
- `SIMULATION_RESULT` is distinct from `VERIFIER_RESULT`.
- The existing independent deterministic verifier remains the sole simulation
  PASS/FAIL authority.

## Deliberate boundaries

C1.4 does not implement provenance storage, source references, confidence,
review, validators, requirement versioning, artifact lineage, Project State,
providers, agents, Ollama, RAG, or UAV execution.

Those belong to later gated work packages.

## Safety/trust boundary

No existing UAV Runtime Core file is modified. A provenance label never grants
PX4/MAVSDK access and never bypasses deterministic verification.
