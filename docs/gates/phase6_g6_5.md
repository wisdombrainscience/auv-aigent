# Gate — Phase 6 G6.5 Schema-Enforced Output

G6.5 is GREEN only when:

- G6.4 is GREEN.
- persisted `TaskRecord.output_schema` is the source of schema selection.
- `engineering_task.v1` deterministically resolves to the existing
  `AgentResult` contract.
- unknown task output schemas fail closed with no fallback.
- the provider-facing schema requires the exact AgentResult top-level envelope,
  `PROPOSED` status, and canonical provenance classifications.
- returned schema snapshots are detached from the immutable registry.
- raw model output is revalidated after generation through the existing strict
  Phase-2 model-output parser.
- invalid JSON, duplicate keys, contract violations, invalid provenance and
  non-finite data retain `MODEL_INVALID_OUTPUT` failure semantics.
- a schema-valid output remains only a proposal and gains no approval/verifier
  authority.
- task status/attempt/dependencies are unchanged.
- schema enforcement is deterministic and survives restart.
- no provider/model call, retry loop, Agent run persistence, context-manifest
  persistence, artifact creation, scheduler assignment, or UAV action is added.
- no Task/Project/Artifact SQLite schema changes are introduced.
- full target regression has zero failures.
- acceptance returns:
  `SCHEMA_ENFORCED_OUTPUT=PASS task=TASK-000001 schema=engineering_task.v1 valid=PASS invalid=REJECTED unknown_schema=REJECTED provenance=PASS restart=PASS provider_calls=none task_state=unchanged run_persistence=reserved_for_G6_6 manifest=reserved_for_G6_7 artifact=reserved_for_G6_8`.

G6.6 Agent run persistence is the sole next step.
