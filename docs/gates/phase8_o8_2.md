# Gate O8.2 — Orchestrator Role

Parent: **O8.1 GREEN / CP7 GREEN**  
Frozen architecture: **RUAV-AI-ARCH-V8.0-BL1**

## Scope

O8.2 adds only the immutable orchestration role profile and logical-agent binding
for the existing MVP `coordinator` role.

## Acceptance requirements

- default RoleRegistry remains exactly the four frozen MVP roles;
- no canonical role ID `orchestrator` is added;
- orchestration capability binds to `coordinator`;
- output boundary is the O8.1 `TaskPlanProposal` contract;
- a logical coordinator AgentInstance can be created without provider/model
  execution;
- O8.2 does not validate/approve/persist/execute the proposal;
- no provider, Project State, TaskStore, PX4, MAVSDK, or Gazebo call occurs;
- full regression remains green.

Acceptance probe:

```bash
python3 tools/check_phase8_orchestrator_role.py
```

Expected semantic marker:

```text
ORCHESTRATOR_ROLE=PASS
```

## Explicitly reserved

```text
O8.3 PlanValidator
O8.4 known-role validation
O8.5 known-schema validation
O8.6 dependency validation
O8.7 resource-budget validation
O8.8 persist approved DAG
```

CP8 remains pending.
