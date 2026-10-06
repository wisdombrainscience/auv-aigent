# Gate — Phase 6 G6.2 AgentInstance

G6.2 is GREEN only when:

- G6.1 is GREEN.
- an immutable logical `AgentInstance` exists.
- each instance has exactly a bounded `instance_key` and G6.1 role definition.
- the default factory accepts all four MVP roles.
- unknown and reserved specialized roles fail closed.
- canonical TASK IDs are valid future instance keys.
- same-role instances with different keys remain distinct.
- creating instances does not mutate the Role Registry.
- no provider/model call occurs.
- no task binding, context, prompt, memory, tools, execution state, result,
  artifact, approval authority, or UAV command capability is added.
- no new canonical ID namespace is invented.
- full target regression has zero failures.
- acceptance returns:
  `AGENT_INSTANCE=PASS instances=2 same_role=PASS isolated_keys=PASS immutable=PASS provider_calls=none task_binding=reserved_for_G6_3 context=reserved_for_G6_4 output=reserved_for_G6_5 run_persistence=reserved_for_G6_6`.

G6.3 Task -> Agent mapping remains the sole next step.
