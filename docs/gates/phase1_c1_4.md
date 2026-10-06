# Gate — Phase 1 C1.4 Provenance Classes

C1.4 may be marked GREEN only when:

- C1.1, C1.2, and C1.3 are GREEN.
- C1.4 preflight passes.
- The eight provenance classes match the frozen baseline exactly.
- Parse/serialize round trips are deterministic.
- Case changes, whitespace, aliases, and unknown classes are rejected.
- Every class has documented semantics.
- USER_FACT is not conflated with VERIFIED_FACT.
- USER_REQUIREMENT and ENGINEERING_ASSUMPTION remain distinct.
- AI_PROPOSAL remains advisory.
- SIMULATION_RESULT remains distinct from VERIFIER_RESULT.
- Existing full regression passes with zero failures.
- No UAV Runtime Core file is modified.
- No persistence, provider, agent, orchestrator, RAG, or UAV bridge is
  introduced early.

Gazebo/SITL is not required because C1.4 is control-plane contract work only.
