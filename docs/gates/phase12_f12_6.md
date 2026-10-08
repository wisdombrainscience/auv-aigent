# F12.6 — Bounded Simulation Retry Gate

Status: candidate until target installer, full regression, and acceptance probe pass.

## Parent

- F12.5 GREEN — Rework Task Generation
- F12.4 GREEN — Lineage Invalidation
- F12.3 GREEN — Issue → Engineering Role Mapping
- F12.2 GREEN — Simulation Issue Taxonomy
- F12.1 GREEN — Verifier Result Parser
- Phase 11 / CP9 GREEN
- U11.3-H1 VERIFIED
- frozen baseline `RUAV-AI-ARCH-V8.0-BL1`

## Acceptance

F12.6 is accepted only when:

1. frozen `simulation.max_retry_attempts = 2` is enforced across the root-run chain;
2. retry is denied until every F12.5 rework task is `DONE`;
3. every stale source has a newer current `APPROVED` version produced by the F12.5 task set;
4. requirement version, mission identity and scenario identity remain unchanged;
5. mapping/authorization/runtime/linkage reuse U11.2/U11.5/U11.6/U11.7;
6. a completed identical retry is restart-idempotent and does not reinvoke runtime;
7. retry reservation is persisted before invocation and crash/invocation failure cannot create unbounded retries;
8. the project-local retry registry has two tables while canonical Project State remains three tables;
9. the independent mission verifier remains the sole simulation verdict authority;
10. no provider/model, direct PX4/MAVSDK instantiation, or `NEEDS_HUMAN` transition occurs.

Acceptance command:

```bash
python3 tools/check_phase12_bounded_simulation_retry.py
```

Expected leading marker:

```text
SIMULATION_BOUNDED_RETRY=PASS
```

F12.6 does not make Phase 12 or CP10 GREEN. F12.7 remains required.
