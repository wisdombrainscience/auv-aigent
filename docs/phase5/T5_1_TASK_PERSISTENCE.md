# T5.1 — Task Persistence

T5.1 is the first Task Engine work package after CP4.

It persists the frozen minimum task contract:

```text
task_id
project_id
title
role
priority
dependencies
input_artifacts
output_schema
validators
review_required
attempt
max_attempts
status
```

## Storage

Each project uses the already-reserved `tasks/` directory:

```text
UAV-PROJ-000001/
└── tasks/
    └── task_registry.sqlite3
```

The registry contains exactly:

```text
task_registry_meta
tasks
```

The Phase-3 Project State database remains unchanged.

## Identity

New task IDs use the existing persistent canonical sequence:

```text
TASK-000001
TASK-000002
...
```

Allocation and insertion are committed together with SQLite ATTACH, so a failed
insert does not silently consume a task ID.

## Creation semantics

T5.1 creation always sets:

```text
status  = CREATED
attempt = 0
```

`max_attempts` is bounded by the frozen default ceiling of 3.

Dependencies and input-artifact references are persisted declarations only in
T5.1. Their existence/DAG semantics are deliberately not decided here.

Role and output-schema names receive syntax/bounds validation only. Known-role
and known-schema policy belongs to later validated planning/agent phases.

## Explicit boundaries

T5.1 does not execute:

```text
T5.2 state machine
T5.3 dependencies
T5.4 DAG cycle detection
T5.5 READY resolver
T5.6 priority queue
T5.7 attempt counter
T5.8 timeout
T5.9 interrupted recovery
T5.10 cancellation
T5.11 concurrency limit
```

No Agent Engine or UAV execution behavior is added.
