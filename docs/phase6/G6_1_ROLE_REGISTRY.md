# G6.1 — Role Registry

G6.1 is the first package of Phase 6 — Single Agent Engine.

The frozen architecture lists ten possible engineering roles, but explicitly
requires the initial MVP to start with only:

```text
coordinator
requirements_engineer
uav_systems_engineer
reviewer
```

G6.1 therefore activates exactly these four logical role IDs. The six
specialized roles remain reserved and are not silently enabled.

## Scope

The role registry provides deterministic known-role lookup only:

```text
contains(role_id)
get(role_id)
require_known(role_id)
definitions()
```

A `RoleDefinition` contains only bounded prompt-free metadata:

```text
role_id
title
purpose
```

It does not contain a model, provider, prompt, context, memory, tools, task
assignment, execution state, approval authority, or UAV command capability.

## Authority

Role existence is not task mapping. G6.3 remains responsible for validating
`TaskRecord.role -> registered role` mapping.

The Reviewer role is advisory only; registering it does not grant approval or
verifier authority.

## Reserved specialized roles

```text
propulsion_power_engineer
airframe_engineer
px4_flight_engineer
software_engineer
simulation_engineer
verification_engineer
```

These may be activated only in a later explicit work package after the core
workflow is stable.

## Preserved boundaries

G6.1 does not:

- instantiate an agent — G6.2;
- map a task to an agent — G6.3;
- build context — G6.4;
- call Model Manager or a provider;
- parse model output — G6.5;
- persist AI runs — G6.6;
- create context manifests — G6.7;
- create DRAFT artifacts — G6.8;
- change Task Engine state or concurrency;
- change UAV execution.
