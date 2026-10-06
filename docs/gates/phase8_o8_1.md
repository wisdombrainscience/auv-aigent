# Gate O8.1 — TaskPlanProposal

Parent: **CP7 GREEN / V7.10 GREEN**  
Frozen architecture: **RUAV-AI-ARCH-V8.0-BL1**

## Scope

O8.1 adds only the bounded non-executable `TaskPlanProposal` contract and its
canonical serializer/parser.

## Acceptance requirements

- proposal is bound to canonical `project_id` and immutable
  `requirement_version`;
- proposed tasks contain task-definition fields but no runtime `attempt/status`;
- canonical JSON round-trip is deterministic;
- duplicate JSON keys / non-standard numeric constants / malformed IDs fail
  closed;
- structurally well-formed but semantically invalid plans remain representable
  for O8.3–O8.7 (duplicate task IDs, unknown role/schema, missing dependency,
  retry/resource policy violations);
- no Project State / TaskStore persistence occurs;
- no provider call occurs;
- no task execution occurs;
- full regression remains green.

Acceptance probe:

```bash
python3 tools/check_phase8_task_plan_proposal.py
```

Expected semantic marker:

```text
TASK_PLAN_PROPOSAL=PASS
```

## Explicitly reserved

```text
O8.2 Orchestrator role
O8.3 PlanValidator
O8.4 known-role validation
O8.5 known-schema validation
O8.6 dependency validation
O8.7 resource-budget validation
O8.8 persist approved DAG
```

CP8 remains pending.
