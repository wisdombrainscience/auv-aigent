# Gate — Phase 5 T5.5 READY/BLOCKED Resolver

T5.5 is GREEN only when:

- T5.4 is GREEN.
- installer preflight/apply pass.
- full target regression has zero failures.
- root CREATED tasks resolve to READY.
- dependent tasks resolve to BLOCKED until every direct dependency is DONE.
- CANCELLED/FAILED dependencies do not satisfy readiness.
- BLOCKED becomes READY when all dependencies later become DONE.
- an incorrectly READY task with an unfinished dependency is corrected to
  BLOCKED.
- repeated resolution is idempotent.
- missing dependencies and cycles fail before any readiness mutation.
- task states outside CREATED/BLOCKED/READY are not overwritten.
- attempt remains unchanged.
- readiness survives restart.
- one project readiness update is transactional.
- Task Registry remains exactly three tables.
- Phase-3 Project State remains exactly three tables.
- no priority queue, attempt increment, timeout, interrupted recovery,
  cancellation orchestration, concurrency, Agent Engine, cloud AI, or UAV
  execution behavior is introduced.
- acceptance returns:
  `TASK_READINESS=PASS root=TASK-000001:DONE child=TASK-000002:READY first_ready=1 first_blocked=1 second_changed=1 restart=PASS registry_tables=3 project_schema_tables=3 priority=reserved_for_T5_6 attempt_counter=reserved_for_T5_7`.

T5.6 priority queue remains the sole next step.
