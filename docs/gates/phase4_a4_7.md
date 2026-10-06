# Gate — Phase 4 A4.7 SUPERSEDED Handling

A4.7 is GREEN only when:

- A4.6 is GREEN.
- installer preflight/apply pass.
- full target regression has zero failures.
- a newer DRAFT version does not displace the current APPROVED version.
- approving a newer version atomically marks lower active versions SUPERSEDED.
- historical lower versions are not rewritten.
- automatic replacement metadata records old-version -> new-version.
- replacement chains remain forward-only and restart-readable.
- at most one version of a logical artifact is normally APPROVED.
- an unrelated STALE/SUPERSEDED required artifact parent blocks APPROVED.
- a replacement version may depend on its own predecessor version.
- Phase-3 SQLite schema remains unchanged.
- replacement does not silently trigger semantic STALE propagation; A4.6 remains
  the explicit invalidation authority.
- no Task Engine, Agent Engine, cloud AI, or UAV execution change is introduced.
- acceptance returns:
  `ARTIFACT_SUPERSEDED=PASS artifact=ART-000001 old=v1:SUPERSEDED current=v2:APPROVED replacement=1->2 restart=PASS registry_tables=6 project_schema_tables=3 graph_tests=reserved_for_A4_8`.

A4.8 graph tests remains the sole next step.
