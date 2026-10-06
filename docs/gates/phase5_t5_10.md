# Gate — Phase 5 T5.10 Deterministic Task Cancellation

T5.10 is GREEN only when:

- T5.9 is GREEN.
- installer preflight/apply pass.
- full target regression has zero failures.
- explicit cancellation reaches CANCELLED from every legal nonterminal/human/
  recovery state.
- CANCELLED is idempotent.
- DONE and FAILED root outcomes cannot be rewritten.
- RUNNING cancellation preserves the consumed attempt and clears execution
  timing metadata atomically.
- exact cancellation does not implicitly cancel dependents.
- a CANCELLED dependency remains unsatisfied for T5.5.
- optional cascade cancels transitive dependents and leaves unrelated tasks
  unchanged.
- cascade requires a valid T5.4 DAG and fails before mutation on cycle/graph
  conflict.
- exact cancellation remains available as a cycle escape hatch.
- cascade is bounded and bound failure occurs before mutation.
- historical DONE/FAILED descendants are not rewritten.
- cancellation survives restart.
- Task Registry remains exactly three tables.
- Phase-3 Project State remains exactly three tables.
- no concurrency control, worker process control, Agent Engine, cloud AI, or
  UAV execution behavior is introduced.
- acceptance returns:
  `TASK_CANCELLATION=PASS root=TASK-000001:CANCELLED descendants=2 running=TASK-000002:CANCELLED metadata=cleared attempt=1 unrelated=READY restart=PASS registry_tables=3 project_schema_tables=3 concurrency=reserved_for_T5_11`.

T5.11 concurrency limit remains the sole next step.
