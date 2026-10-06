# O8.5 — Known-schema validation

O8.5 extends the deterministic Phase-8 `PlanValidator` with output-schema
registry membership checking:

```text
TaskPlanProposal
      ↓
plan.unique_task_ids        ← O8.3
plan.known_roles            ← O8.4
plan.known_output_schemas   ← O8.5
      ↓
ValidationResult
```

## Source of truth

The source is the existing G6.5 `DEFAULT_OUTPUT_SCHEMA_REGISTRY`, because
`ProposedTask.output_schema` maps to `TaskRecord.output_schema` and provider
output enforcement. It is intentionally **not** the Phase-7
`ArtifactSchemaRegistry`, which validates persisted artifact content.

The current frozen enabled schema set is:

```text
engineering_task.v1
```

O8.5 does not silently enable future domain schemas.

## Deterministic evidence

`plan.known_output_schemas` reports:

```text
registered_schema_count
registered_schema_ids
unknown_schemas
unknown_assignments[{task_id, output_schema}]
```

Unknown schema IDs are deduplicated in first-occurrence order while assignment
evidence preserves task order.

## Layering

O8.5 does not inspect dependency existence/self/cycle validity (O8.6), frozen
resource/task/retry/depth policy (O8.7), or persist an approved DAG (O8.8).
It is provider-free, persistence-free, scheduler-free, and UAV-runtime free.
