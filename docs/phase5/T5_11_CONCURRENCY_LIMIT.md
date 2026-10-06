# T5.11 — Deterministic Concurrency Limit

T5.11 closes the Phase-5 Task Engine by enforcing the frozen resource limit:

```text
ai.max_parallel_agents = 1
```

At the Task Engine boundary, one persisted agent-execution slot is consumed by:

```text
ASSIGNED
RUNNING
```

`RESULT_PROPOSED` and later review/validation states no longer consume that
agent-execution slot.

## Global scope

The limit is global across all persisted projects under one local Remote UAV
Project State root.

Therefore this is forbidden:

```text
Project A: TASK-A = ASSIGNED
Project B: TASK-B = ASSIGNED
```

because it would represent two simultaneous local agent execution slots.

## Assignment authority

T5.11 adds:

```text
TaskConcurrencyScheduler.snapshot()
TaskConcurrencyScheduler.assign_specific(project, task)
TaskConcurrencyScheduler.assign_next([project])
```

Global `assign_next()` chooses among valid QUEUED tasks using:

```text
priority DESC
project_id ASC
task_id ASC
```

It performs only:

```text
QUEUED -> ASSIGNED
```

and does not enter RUNNING or invoke any model.

The generic `TaskLifecycleStore` now delegates every
`QUEUED -> ASSIGNED` transition through this concurrency gate, preventing the
hard limit from being bypassed through the lifecycle API.

## Cross-process serialization

Assignment decisions are serialized with:

```text
.task-concurrency.lock
```

under the local Project State root. The lock is only a local coordination
primitive; persisted task status remains the source of truth.

No scheduler lease table or second queue table is added.

## Restart

`ASSIGNED` or `RUNNING` status remains persisted across restart and still
occupies the slot when the scheduler is reconstructed.

T5.9 continues to own the frozen crash rule:

```text
RUNNING after unexpected restart -> INTERRUPTED
```

which releases the execution slot once recovery commits.

## Final Phase-5 boundaries

T5.11 does not:

- spawn or run agents;
- invoke Ollama/models;
- create multiple workers;
- change task dependency/DAG semantics;
- change attempt/timeout policy;
- command PX4/MAVSDK/Gazebo.

Those are later phases. Phase 6 may consume this scheduler but may not bypass
its hard single-agent execution slot.
