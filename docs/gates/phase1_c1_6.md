# Gate — Phase 1 C1.6 ReviewResult

C1.6 may be marked GREEN only when:

- C1.1 through C1.5 are GREEN.
- C1.6 preflight passes.
- ReviewResult contains advisory issues only.
- An issue uses the canonical C1.1 ISSUE namespace.
- The baseline issue fields plus stable fingerprint are enforced.
- Duplicate IDs/fingerprints inside one result are rejected.
- Reviewer output cannot carry PASS/FAIL/APPROVED authority.
- Reviewer output cannot carry artifact patch/replacement fields.
- Parse/serialize behavior is deterministic and immutable.
- Existing full regression passes with zero failures.
- No UAV Runtime Core file is modified.
- No reviewer execution, issue persistence/routing, rework loop, provider,
  Ollama, orchestrator, RAG, or UAV bridge is introduced early.

Cross-review duplicate suppression remains Phase 9 R9.7.

Gazebo/SITL is not required because C1.6 changes control-plane contracts only.
