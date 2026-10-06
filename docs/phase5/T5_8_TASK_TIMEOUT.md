# T5.8 — Deterministic Task Timeout

T5.8 enforces the frozen anti-loop limit:

```text
task.timeout_s = 300
```

When `ASSIGNED -> RUNNING` succeeds, the same Task Registry transaction stores:

```text
execution_attempt
started_at_epoch_s
deadline_at_epoch_s = started_at + 300
```

These are runtime columns on the existing `tasks` table. The Task Registry
therefore remains three tables; immutable `TaskRecord` JSON remains unchanged.

## Timeout behavior

Before the deadline a RUNNING task remains RUNNING. At or after the deadline,
T5.8 atomically applies:

```text
RUNNING -> NEEDS_HUMAN
```

and clears active timeout metadata. This follows the frozen rule that reaching
a hard task limit requires human intervention. The current execution attempt is
not incremented again.

T5.8 emits the structured C1.8 descriptor:

```text
class = TASK
code  = TASK_TIMEOUT
```

The descriptor describes the timeout; it does not authorize retry.

## Restart

The active deadline is persisted, so reopening the Project/Task Registry does
not reset the 300-second budget. T5.8 can still expire the same active RUNNING
attempt after application restart.

Unexpected process-stop recovery is deliberately not implemented here. T5.9
remains the sole owner of the frozen `RUNNING -> INTERRUPTED` restart rule.

## Migration safety

A T5.7 registry with no RUNNING tasks migrates from schema v2 to v3 by adding
runtime timing columns. A pre-existing RUNNING task cannot be migrated safely
because its start time is unknowable; migration therefore fails closed rather
than inventing a new deadline.

## Preserved boundaries

T5.8 has no sleep loop, background watchdog, automatic retry, interrupted
recovery, cancellation orchestration, concurrency control, Agent Engine, or UAV
execution behavior.
