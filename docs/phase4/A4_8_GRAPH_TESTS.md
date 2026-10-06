# A4.8 — Graph Tests

A4.8 is the final work package of Phase 4. It adds verification only; no
Artifact Registry runtime behavior or schema is changed.

The test matrix covers version-specific lineage chains, diamond/fan-out/fan-in
graphs, requirement invalidation, restart persistence, cross-project rejection,
forward/self-edge rejection, STALE transitive propagation, SUPERSEDED currentness,
parent acceptance gates, hash/content stability, registry tamper failure, and
schema invariants.

The suite intentionally tests the interaction of A4.2 versions, A4.3 dependency
edges, A4.4 hashes/content, A4.5 lifecycle, A4.6 STALE propagation, and A4.7
SUPERSEDED handling.

A4.8 does not introduce a general graph engine, task DAG, Agent Engine, or UAV
execution behavior. Task DAG ownership remains Phase 5 and later plan validation.

Passing A4.8 on the target closes CP4 — Artifact Lineage Stable.
