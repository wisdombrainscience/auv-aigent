# R9.6 — Max-Rework Gate

R9.6 enforces the frozen `review.max_rework_attempts: 2` anti-loop policy for
Phase-9 review feedback. It is deliberately separate from the Phase-5 per-task
execution retry budget `TaskRecord.max_attempts=3`.

## Lineage and counting

The budget is scoped to one exact persisted review lineage:

```text
R9.3 review task
  + exact reviewed ART-######@vN
  + R9.4 routed producer execution contract
```

Each persisted R9.5-shaped rework `TaskRecord` in that lineage consumes one
review rework attempt. AI-authored severity/category/field/reason text does not
choose the lineage or budget.

The first and second distinct rework tasks are allowed. Before a third new
rework task is inserted, R9.6 returns a typed `NEEDS_HUMAN` gate decision and
the generator fails closed with no write. Re-requesting an already persisted
issue remains idempotent and returns its existing task even when the lineage
budget is exhausted.

## Human-gate semantics

The frozen architecture says exhausted bounded rework routes to
`NEEDS_HUMAN`. R9.6 represents that as a deterministic gate outcome; it does
not force an unrelated TaskRecord lifecycle transition. This avoids inventing
a project-level review state or violating the frozen task transition graph.

## R9.7 boundary

Different canonical issue IDs that share the same fingerprint are still not
suppressed here. They may consume the two-attempt budget. Cross-issue duplicate
fingerprint suppression remains exclusively R9.7.

R9.6 does not persist standalone issue records, execute tasks, call a provider,
mutate artifacts, or access PX4/MAVSDK/Gazebo.
