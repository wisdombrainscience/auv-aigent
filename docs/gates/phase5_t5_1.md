# Gate — Phase 5 T5.1 Task Persistence

T5.1 is GREEN only when:

- CP4 is GREEN.
- installer preflight/apply pass.
- full target regression has zero failures.
- every new task receives a persistent canonical TASK ID.
- task creation persists the complete frozen minimum task contract.
- creation forces `status=CREATED` and `attempt=0`.
- task records survive restart and list deterministically.
- `max_attempts` is bounded to the frozen ceiling of 3.
- dependencies/input artifacts are bounded declarations but are not resolved yet.
- role/schema names are syntax-checked without prematurely implementing role/schema policy.
- task registry is stored under the reserved `tasks/` directory.
- Phase-3 Project State schema remains exactly unchanged.
- no state transition, DAG, READY resolution, queue, retry increment, timeout,
  interrupted recovery, cancellation, concurrency, agent, cloud-AI, or UAV runtime
  behavior is introduced.
- acceptance returns:
  `TASK_PERSISTENCE=PASS tasks=2 first=TASK-000001 second=TASK-000002 status=CREATED attempt=0 restart=PASS registry_tables=2 project_schema_tables=3 state_machine=reserved_for_T5_2 dependencies=reserved_for_T5_3`.

T5.2 state machine remains the sole next step.
