# Gate — Phase 5 T5.9 INTERRUPTED Restart Recovery

T5.9 is GREEN only when:

- T5.8 is GREEN.
- installer preflight/apply pass.
- full target regression has zero failures.
- persisted RUNNING tasks recover to INTERRUPTED after restart recovery.
- recovered tasks preserve the already-consumed attempt.
- T5.8 execution timing metadata is cleared atomically.
- non-RUNNING states are not changed by restart recovery.
- recovery emits INFRA_TASK_INTERRUPTED failure classification.
- an already-passed old deadline is reported but still recovers to INTERRUPTED.
- repeated recovery is idempotent.
- bounded scan overflow fails before state mutation.
- INTERRUPTED with remaining attempt budget routes to BLOCKED.
- INTERRUPTED with exhausted budget routes to NEEDS_HUMAN.
- routing does not increment attempt.
- BLOCKED retry returns through T5.5 readiness.
- recovery result survives restart.
- Task Registry remains exactly three tables.
- Phase-3 Project State remains exactly three tables.
- no cancellation orchestration, concurrency control, Agent Engine, cloud AI,
  or UAV execution behavior is introduced.
- acceptance returns:
  `TASK_INTERRUPTED=PASS task=TASK-000001 recoveries=2 first=INTERRUPTED retry=BLOCKED exhausted=NEEDS_HUMAN attempts=2 restart=PASS metadata=cleared registry_tables=3 project_schema_tables=3 cancellation=reserved_for_T5_10 concurrency=reserved_for_T5_11`.

T5.10 cancellation remains the sole next step.
