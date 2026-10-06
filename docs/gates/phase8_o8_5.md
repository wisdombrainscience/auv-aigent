# Gate O8.5 — Known-schema validation

Parent: **O8.4 GREEN / CP7 GREEN**  
Frozen architecture: **RUAV-AI-ARCH-V8.0-BL1**

## Scope

O8.5 extends the cumulative `PlanValidator` with exactly one deterministic check:

```text
plan.known_output_schemas
```

Each proposed task `output_schema` must exist in the Phase-6
`DEFAULT_OUTPUT_SCHEMA_REGISTRY`. O8.5 reuses C1.7 `ValidationResult` /
`ValidationCheck` and does not create a parallel result contract.

## Acceptance requirements

- check order is `plan.unique_task_ids`, `plan.known_roles`, then
  `plan.known_output_schemas`;
- `engineering_task.v1` passes;
- a well-formed unregistered schema fails with deterministic task/schema evidence;
- schema source-of-truth is `OutputSchemaRegistry`, not ArtifactSchemaRegistry;
- dependency validation remains deferred to O8.6;
- task/depth/retry/resource policy remains deferred to O8.7;
- approved-DAG persistence remains deferred to O8.8;
- no task executes and no provider is called;
- restart produces the same result;
- full regression remains green.

Acceptance probe:

```bash
python3 tools/check_phase8_known_schema_validation.py
```

Expected marker: `KNOWN_SCHEMA_VALIDATION=PASS`.
