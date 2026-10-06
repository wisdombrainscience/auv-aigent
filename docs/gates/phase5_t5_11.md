# Gate — Phase 5 T5.11 Deterministic Concurrency Limit

T5.11 is GREEN only when:

- T5.10 is GREEN.
- frozen `max_parallel_agents=1` is enforced globally across projects.
- ASSIGNED and RUNNING consume the execution slot.
- RESULT_PROPOSED and later non-execution states release the slot.
- global assignment ordering is priority DESC, project ID ASC, task ID ASC.
- only a valid QUEUED task can be assigned.
- unfinished dependencies and exhausted attempt budgets fail closed.
- direct lifecycle QUEUED -> ASSIGNED cannot bypass the concurrency gate.
- assignment does not increment attempt.
- cancellation and T5.9 INTERRUPTED recovery release the slot.
- persisted ASSIGNED/RUNNING occupancy survives scheduler restart.
- cross-process assignment is serialized by a fail-closed local lock.
- Task Registry remains exactly three tables.
- Phase-3 Project State remains exactly three tables.
- no Agent Engine/model execution/cloud AI/UAV execution behavior is added.
- acceptance returns:
  `TASK_CONCURRENCY=PASS limit=1 first=TASK-000002:900 blocked_while_active=PASS second=TASK-000001:100 global=PASS restart=PASS active=1 registry_tables=3 project_schema_tables=3 cp5=READY`.

If all previous Phase-5 gates are also GREEN, CP5 may close.
