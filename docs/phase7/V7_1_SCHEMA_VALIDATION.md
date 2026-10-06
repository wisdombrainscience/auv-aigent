# V7.1 — Deterministic Schema Validation

## Scope

V7.1 is the first work package in frozen Phase 7 — Deterministic Engineering Trust.
It executes the task-declared `schema` validator against one persisted artifact and
returns the existing C1.7 `ValidationResult` contract.

V7.1 does **not** persist validation results and does **not** change artifact or task
lifecycle state. This preserves the frozen artifact transition order:

```text
DRAFT → REVIEWED → VALIDATED → APPROVED
```

Phase 9 owns review. Later Phase-7 integration work may attach deterministic
validation evidence after the artifact has legally reached the required state.

## Authority

The deterministic schema validator is authoritative only for the schema check it
implements. It does not replace the independent UAV verifier and cannot produce a
simulation/capability/mission verdict.

## Initial schema registry

V7.1 activates exactly one current artifact payload type:

```text
ai.agent_result.v1 → ResultDraftArtifactPayload
```

The validator reuses frozen project contracts rather than introducing an external
schema engine. Exact artifact bytes are first verified by the Phase-4 content store,
then parsed as strict JSON and passed to the G6.8 payload contract.

Strict JSON rejects:

- invalid UTF-8;
- invalid JSON;
- duplicate object keys;
- NaN / Infinity constants;
- non-object top level;
- payloads above the bounded V7.1 size.

## Task binding

The producer `TaskRecord` remains the source of truth for validator selection.
V7.1 runs only when the task declares:

```text
validators: [schema]
```

For `ai.agent_result.v1`, V7.1 also checks deterministic binding of payload
`project_id`, `task_id`, `role_id` and `schema_id` to the persisted artifact/task.

## Result

One schema check is returned through C1.7:

```text
check_id = schema
status   = PASS | FAIL
```

A structurally invalid or unsupported artifact is an engineering `FAIL` result.
Corrupt/missing project state or an unsafe producer-task binding is an operational
conflict and fails closed instead of being disguised as an engineering result.

## Non-goals

V7.1 does not implement:

- V7.2 finite-number checks;
- V7.3 unit validation;
- mass/energy/reserve/thrust/mission calculators;
- V7.9 constraint framework;
- V7.10 validation issue schema;
- DRAFT → REVIEWED;
- REVIEWED → VALIDATED;
- validation-result persistence;
- artifact approval;
- review/rework routing;
- provider/model calls;
- PX4/MAVSDK/Gazebo control.
