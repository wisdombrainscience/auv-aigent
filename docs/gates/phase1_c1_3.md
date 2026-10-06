# Gate — Phase 1 C1.3 Artifact States

C1.3 may be marked GREEN only when all of the following are true on the target:

- C1.1 and C1.2 are already GREEN.
- C1.3 overlay preflight passes.
- Canonical artifact-state set matches the frozen baseline exactly.
- Nominal DRAFT -> REVIEWED -> VALIDATED -> APPROVED path is unit-tested.
- REJECTED, STALE, and SUPERSEDED semantics are unit-tested.
- Malformed state values and illegal direct transitions are rejected.
- State serialization is deterministic.
- APPROVED is not confused with lineage/currentness.
- Existing full regression passes with zero failures.
- No UAV Runtime Core file is modified.
- No persistence, provider, agent, reviewer, orchestrator, RAG, or UAV bridge is
  introduced early.

Gazebo/SITL acceptance is not required for C1.3 because this package changes
control-plane contracts only and does not alter the UAV execution plane.
