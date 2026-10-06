# Gate — Phase 4 A4.2 Artifact Versions

A4.2 is GREEN only when:

- A4.1 is GREEN.
- installer preflight/apply pass.
- full target regression has zero failures.
- new logical artifacts receive persistent canonical ART IDs.
- ART allocation uses the existing Phase-3 canonical sequence.
- version 1 is allocated automatically.
- append increments exactly by one without branching.
- old artifact versions remain readable.
- restart reconstructs the same latest/history.
- logical artifact type remains stable across versions.
- every new version starts DRAFT with validation_result=null.
- Phase-3 SQLite schema remains exactly unchanged.
- dependency existence/cycles are not decided.
- content hash is not calculated or rewritten.
- lifecycle transitions, STALE, SUPERSEDED, cloud AI, and UAV execution changes
  are not introduced.
- acceptance returns:
  `ARTIFACT_VERSIONS=PASS artifact=ART-000001 versions=2 latest=2 old_readable=PASS restart=PASS registry_tables=3 project_schema_tables=3 edges=reserved_for_A4_3 hashing=reserved_for_A4_4`.

A4.3 dependency edges remains the sole next step.
