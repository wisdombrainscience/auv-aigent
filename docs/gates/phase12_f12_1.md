# Gate F12.1 — Verifier Result Parser

## Parent

- U11.7 GREEN.
- Phase 11 GREEN / CP9 GREEN.
- U11.3-H1 Scenario Path Hardening remains VERIFIED.
- Frozen baseline: `RUAV-AI-ARCH-V8.0-BL1`.

## Acceptance

F12.1 passes when all of the following hold:

1. parser input must resolve through an existing project-local U11.7 run link;
2. linked finalized evidence must still pass integrity verification;
3. `finalized.json` and `verification.json` hashes must match the immutable U11.7 anchors;
4. verifier output must match the frozen `MISSION-001` verifier schema;
5. PASS and FAIL verdicts are preserved exactly, never reinterpreted;
6. failed verifier check IDs may be derived deterministically from the verifier result;
7. exact requirement/source-artifact lineage remains attached to the parsed result;
8. provenance remains `VERIFIER_RESULT`;
9. parser performs no persistence or Project State mutation;
10. F12.1 creates no simulation issue taxonomy, owner mapping, lineage invalidation,
    rework task, retry, or `NEEDS_HUMAN` transition.

Target acceptance command:

```bash
python3 tools/check_phase12_verifier_result_parser.py
```

Expected leading marker:

```text
VERIFIER_RESULT_PARSER=PASS
```

F12.1 does not make CP10 GREEN. The simulation-feedback loop remains incomplete
until the later F12.2-F12.7 gates are independently proven on the target.
