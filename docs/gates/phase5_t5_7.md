# Gate — Phase 5 T5.7 Bounded Attempt Counter

T5.7 is GREEN only when:

- T5.6 is GREEN.
- installer preflight/apply pass.
- full target regression has zero failures.
- new tasks still begin with attempt=0.
- READY -> QUEUED and QUEUED -> ASSIGNED do not increment attempt.
- ASSIGNED -> RUNNING increments attempt exactly once and atomically.
- no other transition increments attempt.
- attempt never exceeds max_attempts.
- exhausted ASSIGNED tasks cannot enter RUNNING.
- exhausted READY/QUEUED tasks are rejected by the priority queue.
- REWORK with remaining budget routes to BLOCKED.
- REWORK with exhausted budget routes to NEEDS_HUMAN.
- max_attempts=3 produces attempts 1,2,3 and then NEEDS_HUMAN.
- attempt state survives restart.
- Task Registry remains exactly three tables.
- Phase-3 Project State remains exactly three tables.
- no timeout, interrupted recovery, cancellation orchestration, concurrency,
  Agent Engine, cloud AI, or UAV execution behavior is introduced.
- acceptance returns:
  `TASK_ATTEMPTS=PASS task=TASK-000001 attempts=1-2-3 max=3 exhausted=NEEDS_HUMAN restart=PASS registry_tables=3 project_schema_tables=3 timeout=reserved_for_T5_8 interrupted=reserved_for_T5_9`.

T5.8 timeout remains the sole next step.
