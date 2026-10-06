# CP5 — Task / DAG Engine Stable

CP5 is GREEN only after target acceptance of T5.1 through T5.11.

Required Phase-5 capabilities:

```text
T5.1   task persistence
T5.2   task state machine
T5.3   dependency persistence/existence
T5.4   deterministic DAG/cycle validation
T5.5   READY/BLOCKED resolver
T5.6   deterministic priority queue
T5.7   bounded attempts
T5.8   deterministic timeout
T5.9   RUNNING -> INTERRUPTED restart recovery
T5.10  deterministic cancellation
T5.11  global max_parallel_agents=1 enforcement
```

CP5 preserves:

```text
Project State SQLite tables = 3
Task Registry tables        = 3
AI model execution          = not yet introduced
UAV execution changes       = none
```

The next frozen phase is Phase 6 — Single Agent Engine. Phase 6 must use the
Task Engine scheduler and may not bypass bounded attempts, timeout, recovery,
cancellation, or the single-agent concurrency gate.
