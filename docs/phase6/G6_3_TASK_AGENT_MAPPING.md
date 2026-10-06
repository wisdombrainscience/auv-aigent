# G6.3 — Deterministic Task → Agent Mapping

G6.3 binds one persisted Phase-5 `TaskRecord` to one logical G6.2
`AgentInstance`.

## Mapping rule

The mapping is intentionally simple and deterministic:

```text
TaskRecord.role
      +
TaskRecord.task_id
      ↓
RoleRegistry lookup
      ↓
AgentInstance(
    role = TaskRecord.role,
    instance_key = TaskRecord.task_id
)
```

The task record is the source of truth. G6.3 does not infer a role from title,
natural-language content, output schema, validator names, or AI reasoning.

## Example

```text
TASK-000001
role = uav_systems_engineer
        ↓
uav_systems_engineer:TASK-000001
```

Another task with the same role remains logically isolated:

```text
TASK-000002
role = uav_systems_engineer
        ↓
uav_systems_engineer:TASK-000002
```

## Role gate

The default mapper accepts only the current G6.1 MVP Role Registry.

A persisted task with a syntactically valid but unavailable role, including a
reserved specialized role, fails closed:

```text
propulsion_power_engineer
        ↓
TaskAgentUnknownRoleError
```

G6.3 does not silently substitute `uav_systems_engineer` or another role.

## Lifecycle independence

Mapping does not mutate:

```text
task.status
task.attempt
dependencies
priority
```

A task maps to the same logical agent identity across lifecycle transitions and
application restart because the identity comes only from persisted task ID and
role.

## Preserved boundaries

G6.3 does not:

- assign or schedule tasks;
- consume the Phase-5 concurrency slot;
- build context — G6.4;
- select or invoke a provider/model;
- generate or validate model output — G6.5;
- persist an Agent run — G6.6;
- create context manifests — G6.7;
- create DRAFT artifacts — G6.8;
- command UAV execution.

No schema migration is introduced.
