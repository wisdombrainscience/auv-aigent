# Gate — Phase 6 G6.1 Role Registry

G6.1 is GREEN only when:

- CP5 is GREEN on target.
- exactly the four frozen MVP roles are active.
- all specialized roles remain reserved/inactive.
- role IDs are canonical bounded snake_case.
- duplicate and unknown roles fail closed.
- registry ordering is deterministic.
- role metadata is immutable and prompt-free.
- no provider/model call is introduced.
- no task-to-agent mapping is introduced.
- no AgentInstance/context/output/run persistence/artifact creation is introduced.
- no Task Engine schema/state policy is changed.
- no UAV runtime behavior is changed.
- full target regression has zero failures.
- acceptance returns:
  `ROLE_REGISTRY=PASS roles=4 coordinator=PASS requirements_engineer=PASS uav_systems_engineer=PASS reviewer=PASS specialized=reserved provider_calls=none task_mapping=reserved_for_G6_3 agent_instance=reserved_for_G6_2`.

G6.2 AgentInstance is the sole next step.
