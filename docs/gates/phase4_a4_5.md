# Gate — Phase 4 A4.5 State Transitions

A4.5 is GREEN only when:

- A4.4 is GREEN.
- installer preflight/apply pass.
- full target regression has zero failures.
- registry schema migrates cleanly from A4.3 v2 to A4.5 v3.
- every new artifact version receives one DRAFT lifecycle row.
- persisted transitions reuse the frozen C1.3 transition graph.
- invalid direct transitions fail closed.
- `REVIEWED -> VALIDATED` requires deterministic C1.7 PASS.
- supplied validation failure can reject but cannot validate an artifact.
- `VALIDATED -> APPROVED` requires the persisted PASS validation result.
- historical artifact states remain terminal.
- transition changes do not mutate immutable creation record or content hash.
- restart/open reconstructs current lifecycle state and validation result.
- Phase-3 SQLite schema remains exactly unchanged.
- STALE propagation and automatic SUPERSEDED behavior are not introduced.
- no Task Engine, Agent Engine, cloud-AI, or UAV execution behavior is added.
- acceptance returns:
  `ARTIFACT_STATES=PASS artifact=ART-000001@v1 path=DRAFT-REVIEWED-VALIDATED-APPROVED validation=PASS restart=PASS hash=stable registry_tables=5 project_schema_tables=3 stale=manual superseded=manual`.

A4.6 STALE propagation remains the sole next step.
