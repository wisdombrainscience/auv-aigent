# G6.2 — AgentInstance

G6.2 introduces the minimal logical agent-instance identity for Phase 6.

The frozen architecture states that agents are logical, isolated role/task
instances and that multiple agents do not imply multiple model processes.
G6.2 therefore creates no process, model, provider call, prompt, context,
memory, tool set, task mapping, or project-state mutation.

## Contract

An `AgentInstance` contains only:

```text
instance_key
role
```

`role` is an immutable G6.1 `RoleDefinition`.

`instance_key` is bounded opaque identity text. Canonical task IDs such as:

```text
TASK-000001
```

are valid keys so G6.3 can later use a validated task identity for deterministic
Task -> Agent mapping without inventing a new canonical ID namespace.

## Isolation

Two logical agents may share the same role while remaining distinct:

```text
uav_systems_engineer:TASK-000001
uav_systems_engineer:TASK-000002
```

They are separate `AgentInstance` values because their isolation keys differ.

G6.2 does not claim persistence for these logical instances. G6.6 owns
persistent `AI-RUN-*` execution records.

## Factory

`AgentInstanceFactory` validates the requested role against an injected G6.1
`RoleRegistry`.

The default factory therefore allows only:

```text
coordinator
requirements_engineer
uav_systems_engineer
reviewer
```

The six specialized roles remain reserved.

## Preserved boundaries

G6.2 does not:

- bind a TaskRecord to an instance — G6.3;
- build context — G6.4;
- call Model Manager/Ollama/provider;
- generate or parse model output — G6.5;
- persist an agent run — G6.6;
- create a context manifest — G6.7;
- create DRAFT artifacts — G6.8;
- change Task Engine state/concurrency;
- change UAV execution.

No new canonical ID kind is introduced.
