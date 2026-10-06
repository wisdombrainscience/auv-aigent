# T5.5 — Deterministic READY/BLOCKED Resolver

T5.5 is the sole package that derives readiness from persisted task dependency
completion.

The deterministic rule is:

```text
no direct dependencies                 -> READY
all direct dependencies are DONE       -> READY
otherwise                              -> BLOCKED
```

Only these states are readiness-managed:

```text
CREATED
BLOCKED
READY
```

Execution/review/recovery/human states are not overwritten by T5.5.

## Gate order

Before any task status is mutated:

```text
T5.3 dependency existence
        ↓
T5.4 DAG validation
        ↓
T5.5 READY/BLOCKED resolution
```

A missing dependency or cycle therefore aborts resolution before any status
change.

## Project-wide transaction

For one project, all CREATED/BLOCKED/READY changes are committed in a single
Task Registry transaction. The resolver also checks that the registry node set
did not change between DAG validation and the write transaction.

No dependencies are rewritten and `attempt` remains unchanged.

## Dependency success semantics

Only `DONE` satisfies a dependency. These states do not satisfy readiness:

```text
FAILED
CANCELLED
NEEDS_HUMAN
INTERRUPTED
RUNNING
VALIDATING
...
```

This prevents a dependent task from becoming schedulable merely because a
prerequisite stopped.

## Preserved boundaries

T5.5 does not:

- enqueue READY tasks — T5.6;
- apply priority policy — T5.6;
- increment attempt — T5.7;
- enforce timeout — T5.8;
- recover INTERRUPTED tasks — T5.9;
- orchestrate cancellation — T5.10;
- enforce concurrency — T5.11;
- execute agents or UAV actions.

The Task Registry remains schema v2 with exactly three tables.
