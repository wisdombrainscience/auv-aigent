# Gate — Phase 6 G6.3 Task → Agent Mapping

G6.3 is GREEN only when:

- G6.2 is GREEN.
- one persisted TaskRecord maps deterministically from `task.role`.
- AgentInstance.instance_key equals the canonical persisted task ID.
- two same-role tasks produce distinct logical AgentInstances.
- all four G6.1 MVP roles map successfully.
- unknown and reserved specialized task roles fail closed.
- no fallback/substitution role is silently selected.
- mapping is repeatable and survives restart.
- task status and attempt remain unchanged.
- mapping does not depend on task title or natural-language inference.
- no provider/model call occurs.
- no context, output generation, run persistence, artifact creation, scheduler
  assignment, concurrency consumption, or UAV action is introduced.
- full target regression has zero failures.
- acceptance returns:
  `TASK_AGENT_MAPPING=PASS first=TASK-000001:uav_systems_engineer second=TASK-000002:uav_systems_engineer isolated=PASS reserved_role=REJECTED restart=PASS provider_calls=none task_state=unchanged context=reserved_for_G6_4 output=reserved_for_G6_5 run_persistence=reserved_for_G6_6`.

G6.4 minimal Context Builder remains the sole next step.
