# O8.2 — Orchestrator Role

O8.2 binds the frozen Phase-8 orchestration capability to the existing Phase-6
MVP `coordinator` role.

The frozen architecture's role registry does **not** define a separate canonical
`orchestrator` role ID. Creating one here would change the G6.1 role namespace
and would make O8.4 known-role validation observe a role that was not part of the
frozen baseline. Therefore O8.2 preserves:

```text
RoleRegistry role_id = coordinator
Phase-8 capability   = orchestrator
Output contract      = TaskPlanProposal
```

`OrchestratorRoleProfile` is immutable and prompt/provider/persistence free. It
accepts only an already-structured O8.1 `TaskPlanProposal` as its output
boundary. It does not inspect plan semantics.

A logical orchestrator agent may be created through the existing G6.2
`AgentInstanceFactory`; its role remains `coordinator`. Creating that logical
identity does not spawn a model process or execute work.

## Explicitly not in O8.2

- no new `orchestrator` role ID;
- no PlanValidator;
- no known-role/schema validation;
- no dependency/DAG validation;
- no resource-budget validation;
- no plan approval;
- no TaskStore/Project State persistence;
- no scheduler/task execution;
- no provider/Ollama call;
- no PX4/MAVSDK/Gazebo access.

Those remain owned by O8.3–O8.8 or later phases.
