# Gate — Phase 5 T5.8 Task Timeout

T5.8 is GREEN only when:

- T5.7 is GREEN.
- installer preflight/apply pass.
- full target regression has zero failures.
- frozen timeout is exactly 300 seconds.
- ASSIGNED -> RUNNING persists start/deadline atomically with attempt increment.
- before deadline, RUNNING remains unchanged.
- at/after deadline, RUNNING becomes NEEDS_HUMAN.
- timeout does not increment attempt a second time.
- timeout emits C1.8 `TASK_TIMEOUT` with failure class TASK.
- normal exit from RUNNING clears active timeout metadata.
- active deadline survives application restart and is not reset.
- invalid/non-finite clock input fails closed.
- migration refuses pre-existing RUNNING tasks whose start time is unknowable.
- Task Registry remains exactly three tables.
- Phase-3 Project State remains exactly three tables.
- no retry loop, interrupted recovery, cancellation orchestration, concurrency,
  Agent Engine, cloud AI, or UAV execution behavior is introduced.
- acceptance returns:
  `TASK_TIMEOUT=PASS task=TASK-000001 timeout_s=300 before_deadline=RUNNING at_deadline=NEEDS_HUMAN failure=TASK_TIMEOUT restart=PASS registry_tables=3 project_schema_tables=3 interrupted=reserved_for_T5_9`.

T5.9 interrupted recovery remains the sole next step.
