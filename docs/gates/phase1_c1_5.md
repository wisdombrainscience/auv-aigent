# Gate — Phase 1 C1.5 AgentResult

C1.5 may be marked GREEN only when:

- C1.1 through C1.4 are GREEN.
- C1.5 preflight passes.
- The top-level result envelope matches the frozen baseline exactly.
- Only `status=PROPOSED` is accepted.
- All eight fields are required and unknown top-level fields are rejected.
- Claims require a canonical C1.4 provenance classification.
- Strict JSON compatibility and finite-number checks are enforced.
- Parse/serialize round trips are deterministic.
- Parsed nested content cannot be mutated through caller-owned inputs.
- Existing full regression passes with zero failures.
- No UAV Runtime Core file is modified.
- No provider, persistence, Agent Engine, Ollama, reviewer, validator,
  orchestrator, RAG, or UAV bridge is introduced early.

`MODEL_INVALID_OUTPUT` mapping is intentionally deferred to C1.8 Failure
Taxonomy / later Agent Engine integration; C1.5 only raises `AgentResultError`.

Gazebo/SITL is not required because C1.5 changes control-plane contracts only.
