# T5.2 — Task State Machine

T5.2 makes the frozen C1.2 `TaskState` transition contract persistently executable.

`TaskLifecycleStore.transition_task(...)` performs one direct legal edge and updates the task row atomically. Only `status` changes. The task ID, project, role, priority, dependency declarations, artifact inputs, validators, review flag, attempt, and max-attempts remain unchanged.

The executable state graph is the already-frozen C1.2 graph. Terminal task states remain `DONE`, `FAILED`, and `CANCELLED`. `NEEDS_HUMAN` remains a human gate rather than a terminal state.

T5.2 intentionally does not increment `attempt`; that remains T5.7. It does not resolve dependencies/DAG readiness, queue priority, timeout, restart recovery, cancellation orchestration, or concurrency. Generic transitions to `INTERRUPTED` and `CANCELLED` are legal only because those edges already exist in C1.2; T5.9 and T5.10 still own the automatic recovery/cancellation operations and policy.

The task registry remains schema version 1 with exactly two tables. No Phase-3 Project State schema change is introduced.
