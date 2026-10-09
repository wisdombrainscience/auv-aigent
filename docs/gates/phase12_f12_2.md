# Gate F12.2 — Simulation Issue Taxonomy

## Parent

- F12.1 GREEN.
- Phase 11 GREEN / CP9 GREEN.
- U11.3-H1 remains VERIFIED.
- Frozen baseline `RUAV-AI-ARCH-V8.0-BL1`.

## Acceptance

F12.2 is accepted only when:

1. PASS verifier results yield zero issues;
2. every failed canonical `MISSION-001` verifier check maps to exactly one bounded issue;
3. mapping is deterministic and preserves canonical verifier-check order;
4. C1.8 `FailureDescriptor`/`FailureClass` are reused rather than replaced;
5. behavioral simulation failures use `SIMULATION` while mission/verifier integrity failures use `VERIFICATION`;
6. root-cause fingerprints are deterministic and do not depend on runtime-run observations;
7. requirement/source-artifact/runtime/verifier lineage remains attached to the issue set;
8. provenance remains `VERIFIER_RESULT`;
9. no ISSUE ID is allocated and no persistence occurs;
10. no role mapping, lineage invalidation, rework task, retry, or `NEEDS_HUMAN` transition occurs.

Acceptance command:

```bash
python3 tools/check_phase12_simulation_issue_taxonomy.py
```

Expected leading marker:

```text
SIMULATION_ISSUE_TAXONOMY=PASS
```

F12.2 does not make CP10 GREEN.
