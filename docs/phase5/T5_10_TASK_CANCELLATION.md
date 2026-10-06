# T5.10 — Deterministic Task Cancellation

T5.10 gives cancellation a project-aware, bounded operation instead of relying
on ad-hoc calls to the generic lifecycle transition API.

## Exact cancellation

The default operation is narrow:

```text
cancel_task(project, task, cascade_dependents=false)
```

It changes only the requested task to `CANCELLED`.

`CANCELLED` is idempotent. `DONE` and `FAILED` are immutable terminal outcomes
and cannot be rewritten as cancellation.

All other frozen nonterminal/human/recovery states may be explicitly cancelled
when the C1.2 state graph permits the direct edge.

## RUNNING cancellation

A RUNNING task has already consumed its current attempt. Cancellation therefore
preserves `attempt` and atomically clears T5.8 execution timing metadata:

```text
RUNNING attempt=1
        ↓ cancel
CANCELLED attempt=1
execution_attempt=NULL
started_at_epoch_s=NULL
deadline_at_epoch_s=NULL
```

T5.10 persists cancellation state only. No worker/process signal API exists yet
because Agent Engine execution ownership is introduced in later phases.

## Optional dependent cascade

Hard dependencies mean a child cannot satisfy T5.5 readiness after its required
parent becomes CANCELLED. For an explicit operator request, T5.10 supports:

```text
cancel_task(..., cascade_dependents=true)
```

The cascade:

1. requires T5.4 DAG validation first;
2. follows T5.3 reverse dependency edges;
3. includes transitive dependents;
4. commits selected state changes in one Task Registry transaction;
5. never rewrites historical DONE/FAILED descendants;
6. is bounded by `MAX_CANCELLATION_TARGETS = 4096`.

Cascade is never implicit. Exact cancellation remains available even if the
project graph is invalid, which allows an operator to cancel a task involved in
a malformed cycle without requiring the cycle to validate first.

## Preserved boundaries

T5.10 does not:

- enforce concurrency limits — T5.11;
- spawn/kill worker processes;
- execute agents;
- change dependency declarations or DAG structure;
- increment/reset attempt;
- add a retry loop;
- change Task Registry schema;
- modify UAV execution.
