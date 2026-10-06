# Gate — Phase 6 G6.6 Agent Run Persistence

G6.6 is GREEN only when:

- G6.5 is GREEN.
- an accepted `SchemaEnforcedAgentOutput` can be persisted as one immutable
  canonical `AI-RUN-######` record.
- `AI-RUN-*` IDs are allocated from the existing Project State
  `canonical_id_sequences` `agent_run` row.
- canonical sequence update and per-project run insertion are transactional.
- run IDs remain globally ordered across projects sharing one Project State root.
- persisted identity exactly binds project, task, role, logical agent identity,
  selected output schema, and the G6.5 `AgentResult` proposal.
- persisted output remains `PROPOSED`; persistence adds no approval/verdict.
- the run registry lives under the fixed `ai_runs/` project directory and has
  exactly `agent_run_registry_meta` and `agent_runs` tables.
- Project State remains exactly three tables; Task/Artifact schemas are unchanged.
- persisted records reopen after restart and corruption/tampering fails closed.
- a failed insertion does not consume an `AI-RUN-*` sequence number.
- task state/attempt/dependencies are unchanged.
- no provider/model call, retry/reflection loop, context manifest, artifact
  creation, reviewer/validator action, scheduler mutation, or UAV action is added.
- full target regression has zero failures.
- acceptance returns:
  `AGENT_RUN_PERSISTENCE=PASS first=AI-RUN-000001 second=AI-RUN-000002 task=TASK-000001 schema=engineering_task.v1 proposal=PASS restart=PASS global_ids=PASS registry_tables=2 project_schema_tables=3 task_state=unchanged provider_calls=none manifest=reserved_for_G6_7 artifact=reserved_for_G6_8`.

G6.7 context manifest is the sole next step.
