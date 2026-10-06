# Gate — Phase 5 T5.2 Task State Machine

T5.2 is GREEN only when T5.1 is GREEN; installer preflight/apply pass; all frozen C1.2 direct transitions are executable; illegal/self transitions fail without mutation; terminal states have no successors; NEEDS_HUMAN remains recoverable by explicit human-directed edges; transitions persist across restart; only status changes; attempt remains unchanged; the task registry remains two tables; Phase-3 Project State remains three tables; and no T5.3+ dependency/DAG/queue/attempt/timeout/recovery/cancellation-orchestration/concurrency behavior is added.

Acceptance must return:

`TASK_STATES=PASS task=TASK-000001 path=CREATED-READY-QUEUED-ASSIGNED-RUNNING-RESULT_PROPOSED-VALIDATING-DONE attempt=0 restart=PASS registry_tables=2 project_schema_tables=3 dependencies=reserved_for_T5_3 attempt_counter=reserved_for_T5_7`

T5.3 dependencies is the sole next step.
