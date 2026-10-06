# C1.2 — Task States

Status: candidate for target gate.

## Scope

C1.2 defines only the persisted task-state vocabulary and deterministic direct
transition contract for the V8 local-AI engineering control plane.

Canonical states are exactly:

`CREATED, BLOCKED, READY, QUEUED, ASSIGNED, RUNNING, RESULT_PROPOSED,
REVIEWING, VALIDATING, DONE, REWORK, FAILED, INTERRUPTED, NEEDS_HUMAN,
CANCELLED`.

The contract also implements the frozen crash-recovery rule:

`RUNNING -> INTERRUPTED` after an unexpected process stop.

## Deliberate boundaries

C1.2 does not implement a scheduler, database, attempts, retry counters,
providers, Ollama, agents, reviewers, validators, orchestration, RAG, or any
UAV/PX4 behavior.

`NEEDS_HUMAN` is an automation halt, not a final outcome. Automated execution
must stop there. A later explicit human decision may return the task to
`BLOCKED`/`READY` or close it as `FAILED`/`CANCELLED`.

`DONE`, `FAILED`, and `CANCELLED` are terminal outcomes for one task record.

The state contract permits `RESULT_PROPOSED -> VALIDATING` because the frozen
task schema contains `review_required`; tasks that require review use
`RESULT_PROPOSED -> REVIEWING -> VALIDATING`.

Retry/rework limits are intentionally not implemented here. The frozen numeric
limits remain authoritative and will be enforced by the later Task Engine.

## Safety/trust boundary

No file in the existing UAV Runtime Core is modified. This work package cannot
command PX4, MAVSDK, Gazebo, motors, actuators, capabilities, or missions.
