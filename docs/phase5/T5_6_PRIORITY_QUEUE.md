# T5.6 — Deterministic Priority Queue

T5.6 converts READY tasks into the persisted `QUEUED` lifecycle state and
orders queued tasks deterministically.

## Priority policy

T5.6 freezes the numeric priority policy:

```text
larger priority value = earlier queue position
```

For equal priority, canonical task ID ascending is the stable tie-breaker:

```text
priority DESC, TASK ID ASC
```

This keeps queue order deterministic across process restart and does not require
a separate mutable queue-order table.

## Queue membership

The queue is derived from persisted task state:

```text
READY --T5.6--> QUEUED
```

`enqueue_ready(project)` first runs the T5.5 READY/BLOCKED resolver and T5.4
DAG validation, then transitions every current READY task to QUEUED in one Task
Registry transaction.

Existing QUEUED tasks remain queued. Re-running the operation is idempotent.

## Safety check

A QUEUED task must still have every direct dependency in `DONE`. If a task was
manually forced to QUEUED while a parent is unfinished, queue reads fail closed
instead of presenting that task to later scheduling stages.

## No assignment yet

T5.6 exposes:

```text
enqueue_ready(project)
ordered_queue(project)
peek_next(project)
```

`peek_next()` is read-only. T5.6 does **not** move tasks to ASSIGNED or RUNNING.
Execution slots/concurrency remain T5.11 concerns, while attempt accounting is
T5.7.

## Preserved boundaries

T5.6 does not:

- increment `attempt` — T5.7;
- enforce timeout — T5.8;
- recover INTERRUPTED tasks — T5.9;
- orchestrate cancellation — T5.10;
- enforce concurrency/resource slots — T5.11;
- spawn workers or run agents;
- modify UAV execution.

Task Registry schema remains v2 with exactly three tables. Phase-3 Project State
remains exactly three tables.
