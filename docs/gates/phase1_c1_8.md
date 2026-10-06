# Gate — Phase 1 C1.8 Failure Taxonomy

C1.8 may be marked GREEN only when:

- C1.1 through C1.7 are GREEN.
- C1.8 preflight passes.
- All eleven frozen failure classes are represented distinctly.
- Machine-readable codes are category-prefixed and canonical.
- `MODEL_INVALID_OUTPUT` is representable without introducing provider logic.
- Failure details are immutable strict JSON with finite numbers.
- Failure records cannot authorize retry, state transition, or simulation verdict.
- SIMULATION and VERIFICATION remain separate failure domains.
- Existing full regression passes with zero failures.
- No UAV Runtime Core file is modified.
- No exception mapper, retry engine, persistence, provider, Ollama, agent
  execution, orchestrator, RAG, or UAV bridge is introduced early.

Gazebo/SITL is not required because C1.8 changes control-plane contracts only.
