# Gate — Phase 4 A4.3 Dependency Edges

A4.3 is GREEN only when:

- A4.2 is GREEN.
- installer preflight/apply pass.
- full target regression has zero failures.
- registry schema migrates cleanly from A4.2 v1 to A4.3 v2.
- dependency edges persist in ArtifactRecord declaration order.
- requirement dependencies resolve to an existing REQ in the referenced
  RequirementSet version.
- artifact dependencies resolve to an already-existing artifact version in the
  same project.
- forward/self artifact-version dependencies fail closed.
- edge rows and immutable ArtifactRecord declarations cannot drift silently.
- dependency lineage survives restart.
- direct dependents are queryable through a bounded read-only API.
- Phase-3 SQLite schema remains exactly unchanged.
- no hash computation, state transition execution, STALE propagation,
  SUPERSEDED handling, Task Engine, Agent Engine, cloud AI, or UAV execution
  change is introduced.
- acceptance returns:
  `ARTIFACT_EDGES=PASS ... targets=verified restart=PASS registry_tables=4 project_schema_tables=3 hashing=reserved_for_A4_4 states=reserved_for_A4_5`.

A4.4 hashing remains the sole next step.
