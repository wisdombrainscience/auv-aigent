# O8.1 — TaskPlanProposal

O8.1 introduces the frozen Phase-8 proposal boundary:

```text
Orchestrator
    ↓
TaskPlanProposal
    ↓
PlanValidator (O8.3+)
    ↓
Approved Task DAG (O8.8)
```

This gate defines only an immutable, bounded, non-executable proposal contract.
It does not add the Orchestrator role, approve plans, persist tasks/DAGs, or
execute agents.

## Contract

A `TaskPlanProposal` binds one project and one immutable requirement snapshot:

```text
project_id
requirement_version
tasks[]
```

Each `ProposedTask` carries the task-definition fields needed by the existing
Task Engine:

```text
task_id
title
role
priority
dependencies
input_artifacts
output_schema
validators
review_required
max_attempts
```

It deliberately does **not** carry runtime/persistence fields:

```text
project_id  (owned by the proposal envelope)
attempt
status
AgentInstance
AI-RUN ID
scheduler state
```

Candidate `TASK-######` values are format-only proposal identifiers. O8.1 does
not call the Project State canonical-ID allocator and does not consume persistent
TASK sequences.

## Layering preserved for later Phase-8 gates

O8.1 validates JSON/wire structure, canonical ID syntax, string syntax, bounded
arrays, duplicate entries that TaskRecord itself cannot represent, and stable
canonical serialization. It intentionally does **not** perform semantic plan
approval checks.

Therefore a structurally valid O8.1 proposal may still contain:

- duplicate task IDs — O8.3 PlanValidator;
- unknown well-formed roles — O8.4;
- unknown well-formed output schemas — O8.5;
- missing/self/cyclic dependencies — O8.6;
- more than the frozen 30-task policy or retry counts above the frozen runtime
  policy — O8.7.

The O8.1 parser has defensive finite bounds (`256` proposed tasks, `512 KiB`
serialized JSON, and `1000` proposed attempts) so malformed input cannot be
unbounded before O8.7 applies the tighter frozen policies. These defensive
bounds are **not** approval policy.

## Serialization

`TaskPlanProposal.to_json_bytes()` and `serialize_task_plan_proposal()` emit
UTF-8 canonical compact JSON with sorted object keys. The parser rejects:

- invalid UTF-8;
- duplicate JSON object keys;
- `NaN`, `Infinity`, and `-Infinity` constants;
- unknown/missing object fields;
- non-canonical Project/Task IDs;
- malformed role/schema/validator names;
- unbounded payloads.

Task order and dependency order are preserved.

## Non-goals

O8.1 does not:

- register or run an Orchestrator agent;
- inspect the RoleRegistry or OutputSchemaRegistry;
- validate dependency existence or DAG acyclicity;
- enforce `max_tasks_per_plan=30`, dependency-depth, or retry policy;
- create TaskRecord rows or task-registry files;
- persist an approved DAG;
- call Ollama or any provider;
- change artifact/task lifecycle state;
- touch PX4, MAVSDK, Gazebo, or verifier authority.
