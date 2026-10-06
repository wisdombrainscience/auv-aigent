# Gate O8.4 — Known-role validation

Parent: **O8.3 GREEN / CP7 GREEN**  
Frozen architecture: **RUAV-AI-ARCH-V8.0-BL1**

## Scope

O8.4 extends the existing `PlanValidator` with one deterministic check:

```text
plan.known_roles
```

Each proposed task role must exist in the frozen Phase-6 `RoleRegistry`.
O8.4 reuses C1.7 `ValidationResult` / `ValidationCheck` and does not create a
parallel result schema.

## Acceptance requirements

- default PlanValidator check order is `plan.unique_task_ids`, then
  `plan.known_roles`;
- all four frozen MVP roles pass;
- a well-formed unregistered role fails with deterministic task/role evidence;
- reserved specialized roles are not silently enabled;
- `orchestrator` is not introduced as a new role ID;
- unknown-schema validation remains deferred to O8.5;
- dependency validation remains deferred to O8.6;
- resource/task/retry policy remains deferred to O8.7;
- no approved DAG is produced or persisted;
- no task executes and no provider is called;
- restart produces the same result;
- full regression remains green.

Acceptance probe:

```bash
python3 tools/check_phase8_known_role_validation.py
```

Expected semantic marker:

```text
KNOWN_ROLE_VALIDATION=PASS
```

## Explicitly reserved

```text
O8.5 known-schema validation
O8.6 dependency validation
O8.7 resource-budget validation
O8.8 persist approved DAG
```

CP8 remains pending.
