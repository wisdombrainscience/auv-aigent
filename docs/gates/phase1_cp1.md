# CP1 Gate — Contracts Stable

Phase 1 / CP1 is GREEN only when all conditions below are satisfied on the
target deployment tree.

- CP0 remains the frozen parent baseline.
- C1.1 Canonical IDs GREEN.
- C1.2 Task States GREEN.
- C1.3 Artifact States GREEN.
- C1.4 Provenance Classes GREEN.
- C1.5 AgentResult GREEN.
- C1.6 ReviewResult GREEN.
- C1.7 ValidationResult GREEN.
- C1.8 Failure Taxonomy GREEN.
- C1.9 integration/serialization/invalid-state tests GREEN.
- Full target regression has zero failures.
- Contract modules do not import PX4/Gazebo/UAV execution-plane modules.
- No unbounded retry/sleep loop is introduced.
- No Ollama/provider execution is introduced before CP1 closes.
- No Project State, scheduler, Agent Engine, Orchestrator, RAG, or UAV Bridge is
  introduced early.
- Independent UAV verifier authority remains unchanged.

Gazebo/SITL acceptance is not required for C1.9 because Phase 1 has not changed
the UAV execution plane.

Once CP1 is GREEN, Phase 1 is closed. The next authorized work is:

Phase 2 — Local Provider
  P2.1 LocalAIProvider interface
  P2.2 MockProvider
  P2.3 OllamaProvider
  P2.4 timeouts / malformed JSON / unavailable provider tests
  P2.5 provider health and bounded cleanup

Exact Phase-2 work-package slicing should be finalized before implementation.
