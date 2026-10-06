# Gate O8.3 — PlanValidator

Parent: **O8.2 GREEN / CP7 GREEN**  
Frozen architecture: **RUAV-AI-ARCH-V8.0-BL1**

## Scope

O8.3 adds the deterministic `PlanValidator` boundary for an O8.1
`TaskPlanProposal` and implements only the semantic rule explicitly deferred to
this gate: proposed task IDs must be unique.

The result reuses the existing C1.7 `ValidationResult` / `ValidationCheck`
contract. O8.3 does not create a parallel validation-result schema.

## Acceptance requirements

- one deterministic `plan.unique_task_ids` check is produced;
- unique task IDs return PASS;
- duplicate task IDs return FAIL with deterministic duplicate-ID evidence;
- unknown roles remain deferred to O8.4;
- unknown output schemas remain deferred to O8.5;
- dependency existence / acyclicity remains deferred to O8.6;
- frozen task-count / depth / retry / resource policy remains deferred to O8.7;
- no approved DAG is produced or persisted;
- no task executes and no provider is called;
- restart produces the same validation result;
- full regression remains green.

Acceptance probe:

```bash
python3 tools/check_phase8_plan_validator.py
```

Expected semantic marker:

```text
PLAN_VALIDATOR=PASS
```

## Explicitly reserved

```text
O8.4 known-role validation
O8.5 known-schema validation
O8.6 dependency validation
O8.7 resource-budget validation
O8.8 persist approved DAG
```

CP8 remains pending.
