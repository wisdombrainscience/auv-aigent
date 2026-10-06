# T5.9 — INTERRUPTED Restart Recovery

T5.9 implements the frozen crash-recovery invariant:

```text
task persisted as RUNNING
        +
unexpected process stop / restart
        ↓
INTERRUPTED
```

## Recovery operation

`TaskInterruptedRecoveryStore.recover_project(project)` scans the persisted
Task Registry for `RUNNING` tasks.

For each recovered task, one transaction changes:

```text
status: RUNNING -> INTERRUPTED
attempt: unchanged
execution_attempt: NULL
started_at_epoch_s: NULL
deadline_at_epoch_s: NULL
```

The attempt was already consumed when the prior execution entered RUNNING, so
restart recovery must not increment or reset it.

## Failure classification

Each recovered RUNNING task produces a C1.8 descriptor:

```text
class = INFRA
code  = INFRA_TASK_INTERRUPTED
```

The descriptor records the old start/deadline and whether that deadline had
already passed.

Restart interruption takes precedence for a task discovered RUNNING during
restart recovery. Even if the old timeout deadline has passed, the frozen rule
still recovers that task to `INTERRUPTED`; the report preserves
`deadline_passed=true`.

## Explicit routing

Recovery does not silently retry.

```text
INTERRUPTED + attempt < max_attempts
        ↓
BLOCKED
```

T5.5 then re-evaluates dependencies before another retry can become READY.

When the execution budget is exhausted:

```text
INTERRUPTED + attempt >= max_attempts
        ↓
NEEDS_HUMAN
```

No automatic loop exists.

## Scope

Only persisted `RUNNING` tasks are recovered. T5.9 does not invent policy for
`ASSIGNED`, `QUEUED`, or other states because the frozen baseline specifically
requires `RUNNING -> INTERRUPTED` after crash/restart.

## Bounds

```text
MAX_INTERRUPTED_RECOVERIES_PER_SCAN = 4096
```

If the scan exceeds the bound, recovery fails before state mutation.

## Preserved boundaries

T5.9 does not:

- orchestrate cancellation — T5.10;
- enforce concurrency — T5.11;
- spawn workers or agents;
- sleep/retry in a loop;
- modify task dependency/DAG policy;
- change the Task Registry schema;
- modify UAV execution.
