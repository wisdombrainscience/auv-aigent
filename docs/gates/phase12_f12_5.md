# F12.5 — Rework Task Generation Gate

Status: candidate until target installer, full regression, and acceptance probe pass.

## Parent

- F12.4 GREEN — Lineage Invalidation
- F12.3 GREEN — Issue → Engineering Role Mapping
- F12.2 GREEN — Simulation Issue Taxonomy
- F12.1 GREEN — Verifier Result Parser
- Phase 11 / CP9 GREEN
- U11.3-H1 VERIFIED
- frozen baseline `RUAV-AI-ARCH-V8.0-BL1`

## Acceptance

F12.5 is accepted only when:

1. PASS feedback creates zero tasks;
2. FAIL feedback creates at most one persisted task per distinct routed active owner;
3. current MVP feedback therefore creates exactly one `uav_systems_engineer` task;
4. F12.3/F12.4 project, requirement, source-lineage, run, verifier-hash,
   fingerprints and provenance must match before persistence;
5. exact APPROVED simulation requirement version is used as bounded task context;
6. F12.4 `STALE` source artifacts are not inserted into agent task inputs;
7. task starts `CREATED`, attempt 0, max attempts 3, review required, using
   `engineering_task.v1`;
8. repeated generation for one feedback event is restart-idempotent;
9. a new runtime event receives a new task identity even for the same root cause;
10. persistence reuses the existing task registry only;
11. no ISSUE ID, simulation retry, NEEDS_HUMAN transition, provider call, or PX4 access occurs.

Acceptance command:

```bash
python3 tools/check_phase12_simulation_rework_task_generation.py
```

Expected leading marker:

```text
SIMULATION_REWORK_TASK_GENERATION=PASS
```

F12.5 does not make Phase 12 or CP10 GREEN.
