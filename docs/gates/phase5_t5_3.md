# Gate — Phase 5 T5.3 Task Dependencies

T5.3 is GREEN only when:

- T5.2 is GREEN.
- installer preflight/apply pass.
- full target regression has zero failures.
- task dependency declarations are persisted as ordered edges.
- edge rows remain exactly consistent with immutable TaskRecord declarations.
- direct dependencies and direct dependents are deterministic and bounded.
- `verify_project()` rejects missing/cross-project dependency targets.
- dependency verification survives restart.
- a clean T5.1/T5.2 schema-v1 registry migrates to schema v2 and backfills edges.
- Phase-3 Project State remains exactly three tables.
- dependency verification does not mutate task status or attempts.
- no DAG cycle detection is implemented; T5.4 remains authoritative.
- no READY resolution, priority queue, attempt increment, timeout, restart
  recovery, cancellation orchestration, concurrency, Agent Engine, cloud AI,
  or UAV execution behavior is introduced.
- acceptance returns:
  `TASK_DEPENDENCIES=PASS root=TASK-000001 child=TASK-000002 edges=1 existence=PASS reverse=PASS restart=PASS registry_tables=3 project_schema_tables=3 cycles=reserved_for_T5_4 ready=reserved_for_T5_5`.

T5.4 DAG cycle detection remains the sole next step.
