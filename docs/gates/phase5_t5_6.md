# Gate — Phase 5 T5.6 Priority Queue

T5.6 is GREEN only when:

- T5.5 is GREEN.
- installer preflight/apply pass.
- full target regression has zero failures.
- READY tasks transition to QUEUED transactionally.
- BLOCKED tasks are not queued.
- larger numeric priority sorts before smaller numeric priority.
- equal priority uses canonical TASK ID ascending as deterministic tie-breaker.
- repeated enqueue is idempotent.
- newly-ready higher-priority work is ordered ahead of lower-priority queued work.
- queue reads fail closed if a QUEUED task has an unfinished dependency.
- cycle/missing dependency fails before queue mutation.
- `peek_next()` is read-only and no task is moved to ASSIGNED/RUNNING.
- attempt remains unchanged.
- queue order survives restart.
- Task Registry remains exactly three tables.
- Phase-3 Project State remains exactly three tables.
- no attempt counter, timeout, interrupted recovery, cancellation orchestration,
  concurrency control, Agent Engine, cloud AI, or UAV execution behavior is
  introduced.
- acceptance returns:
  `TASK_PRIORITY_QUEUE=PASS queued=4 first=TASK-000002:900 ties=canonical_id attempt=0 restart=PASS registry_tables=3 project_schema_tables=3 attempt_counter=reserved_for_T5_7 concurrency=reserved_for_T5_11`.

T5.7 attempt counter remains the sole next step.
