# T5.7 — Bounded Attempt Counter

T5.7 makes the persisted `attempt` field authoritative.

## Attempt meaning

A task starts with:

```text
attempt = 0
```

Queueing and assignment do not consume an attempt.

The legal execution transition:

```text
ASSIGNED -> RUNNING
```

atomically becomes:

```text
status  = RUNNING
attempt = previous_attempt + 1
```

No other state transition increments the counter.

This means `attempt=1` is the first execution attempt, `attempt=2` the second,
and so on.

## Hard bound

The frozen anti-loop policy remains:

```text
task.max_attempts <= 3
```

`ASSIGNED -> RUNNING` is rejected when:

```text
attempt >= max_attempts
```

so a task can never persist `attempt > max_attempts`.

T5.7 also hardens the T5.6 queue so an exhausted READY/QUEUED task fails
closed rather than being scheduled again.

## Rework routing

T5.7 adds a deterministic budget gate for REWORK:

```text
REWORK + remaining budget
        ↓
     BLOCKED
```

`BLOCKED` is intentional. T5.5 must resolve dependencies again before the
retry can return to READY.

When the budget is exhausted:

```text
REWORK + attempt == max_attempts
        ↓
    NEEDS_HUMAN
```

This directly implements the frozen anti-loop rule:

```text
limit reached -> NEEDS_HUMAN
```

There is no automatic retry loop.

## Persistence and restart

`attempt` stays inside the existing immutable TaskRecord JSON snapshot and is
updated transactionally with the RUNNING status. No Task Registry schema change
is introduced.

## Preserved boundaries

T5.7 does not:

- enforce execution timeout — T5.8;
- recover INTERRUPTED work — T5.9;
- orchestrate cancellation — T5.10;
- enforce concurrency — T5.11;
- sleep/retry in a loop;
- spawn agents;
- modify UAV execution.
