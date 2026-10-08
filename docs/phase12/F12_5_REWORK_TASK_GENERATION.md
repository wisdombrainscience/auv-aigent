# F12.5 — Rework Task Generation

## Purpose

F12.5 materializes routed simulation feedback as bounded persisted engineering
`TaskRecord` values using the existing Phase-5 task registry. It does not create
a second task subsystem and does not reinterpret the independent verifier verdict.

## Parent contracts

F12.5 accepts only a matching pair of:

- F12.3 `SimulationIssueRolePlan`; and
- F12.4 `SimulationLineageInvalidationResult`.

Project ID, requirement version, exact source artifact lineage, runtime run ID,
mission/scenario IDs, verifier SHA-256, issue fingerprints, and
`VERIFIER_RESULT` provenance must match exactly before any task persistence.

## Owner grouping

Tasks are grouped by routed engineering owner, not by artifact and not by failed
check. Therefore one feedback event creates at most one task per distinct active
owner. The current frozen MVP maps every simulation issue to
`uav_systems_engineer`, so one verifier FAIL creates at most one F12.5 task.

The bound is:

```text
MAX_SIMULATION_REWORK_TASKS = 7
```

which is below the frozen planner `max_tasks_per_plan = 30` limit.

## Task identity and restart idempotency

The task title contains a deterministic SHA-256 feedback identity over:

- project ID;
- exact requirement version;
- exact simulation source-artifact lineage;
- runtime run ID;
- mission/scenario IDs;
- verifier evidence SHA-256;
- routed owner role; and
- that owner's ordered issue fingerprints.

Repeated generation for the same feedback event reuses the same persisted task.
A later simulation run receives a different identity even if the classified root
cause is unchanged.

## Task execution contract

The generated task uses the existing task engine contract:

```text
role            = routed active engineering owner
priority        = 0
dependencies    = []
output_schema   = engineering_task.v1
validators      = []
review_required = true
attempt         = 0
max_attempts    = 3
status          = CREATED
```

F12.4 has already made the simulation source artifacts `STALE`. The existing
Minimal Context boundary accepts only `APPROVED` artifact inputs, so F12.5 does
**not** insert those stale artifacts into `task.input_artifacts`. Instead it binds
the task to the exact APPROVED requirement version used by the simulation. The
full stale source lineage remains preserved in the immutable F12.5 generation
result and in the deterministic feedback identity, while the authoritative run
link remains persisted by U11.7.

Requirement context is bounded by the existing task input limit of 64. If the
exact approved requirement version exceeds that bound, generation fails closed
before task persistence.

## Persistence

F12.5 adds no database or schema. Persistence is the existing Phase-5 task
registry. No canonical ISSUE record is created.

## Not in F12.5

F12.5 deliberately does not:

- execute the generated task;
- allocate/persist `ISSUE-*`;
- authorize or invoke a simulation retry — F12.6;
- transition task/project state to `NEEDS_HUMAN` — F12.7;
- call a provider/model;
- access PX4/MAVSDK;
- change the independent verifier verdict.

Phase 12 and CP10 remain pending after F12.5.
