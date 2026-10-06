# Gate — Phase 5 T5.4 Deterministic Task DAG

T5.4 is GREEN only when:

- T5.3 is GREEN.
- installer preflight/apply pass.
- full target regression has zero failures.
- missing/cross-project dependencies fail before DAG classification.
- self-cycles and multi-node cycles are detected.
- cycle witnesses are deterministic.
- chain, diamond, disconnected, and empty DAGs validate.
- valid DAGs produce a deterministic topological order.
- graph validation survives restart.
- graph validation does not mutate status or attempt.
- bounded graph scans fail closed.
- Task Registry remains exactly three tables.
- Phase-3 Project State remains exactly three tables.
- no READY/BLOCKED resolver, priority queue, attempt increment, timeout,
  interrupted recovery, cancellation, concurrency, Agent Engine, cloud AI, or
  UAV execution behavior is introduced.
- acceptance returns:
  `TASK_DAG=PASS nodes=4 edges=4 topological=PASS diamond=PASS self_cycle=PASS restart=PASS registry_tables=3 project_schema_tables=3 ready=reserved_for_T5_5`.

T5.5 READY resolver remains the sole next step.
