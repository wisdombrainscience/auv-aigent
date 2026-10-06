# Gate — Phase 1 C1.2 Task States

C1.2 may be marked GREEN only when all of the following are true on the target
tree:

- C1.1 is already GREEN.
- C1.2 overlay preflight passes.
- Canonical task-state set matches the frozen baseline exactly.
- Legal and illegal direct transitions are unit-tested.
- Terminal outcomes and the NEEDS_HUMAN human gate are unit-tested.
- Crash recovery maps RUNNING to INTERRUPTED.
- Serialization/parse behavior is deterministic and rejects malformed input.
- Existing full regression passes with zero failures.
- No UAV Runtime Core file is modified.
- No retry loop, scheduler, provider, agent, Ollama, RAG, or UAV bridge is
  introduced early.

Gazebo/SITL acceptance is not required for C1.2 because the work package is
control-plane contracts only and does not alter the UAV execution plane.
