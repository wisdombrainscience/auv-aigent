# O8.4 — Known-role validation

O8.4 extends the deterministic Phase-8 `PlanValidator` with RoleRegistry
membership checking.

```text
TaskPlanProposal
      ↓
plan.unique_task_ids   ← O8.3
plan.known_roles       ← O8.4
      ↓
ValidationResult
```

## Source of truth

O8.4 does not define a second role list. It consumes the frozen Phase-6
`DEFAULT_ROLE_REGISTRY`, whose MVP role IDs remain:

```text
coordinator
requirements_engineer
reviewer
uav_systems_engineer
```

The orchestration capability introduced by O8.2 remains bound to
`coordinator`; `orchestrator` is not a new role ID. Reserved specialized role
IDs are not silently enabled by O8.4.

## Deterministic evidence

`plan.known_roles` reports the registered role IDs plus unknown role assignments
as `{task_id, role}` objects. Unknown role names are deduplicated in first
occurrence order while assignment evidence preserves task order.

## Layering

O8.4 does not inspect:

- output-schema registry membership — O8.5;
- dependency existence/self/cycle validity — O8.6;
- task count, dependency depth, retry/resource policy — O8.7;
- approved DAG persistence — O8.8.

Therefore a proposal may pass O8.4 while still containing a well-formed unknown
schema, missing dependency, or retry count above the frozen execution policy.

O8.4 remains provider-free, persistence-free, scheduler-free, and UAV-runtime
free.
