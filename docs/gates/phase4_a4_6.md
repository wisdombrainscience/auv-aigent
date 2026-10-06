# Gate — Phase 4 A4.6 STALE Propagation

A4.6 is GREEN only when:

- A4.5 is GREEN.
- installer preflight/apply pass.
- full target regression has zero failures.
- one explicitly-invalid artifact root propagates STALE to every direct and
  transitive dependent artifact version.
- exact requirement-version invalidation propagates through artifact lineage.
- unrelated artifacts remain unchanged.
- historical REJECTED/STALE/SUPERSEDED versions are not rewritten.
- traversal continues through historical nodes to protect downstream current
  artifacts.
- APPROVED is rejected when a required artifact dependency is STALE.
- propagation is bounded and transactional.
- restart preserves propagated STALE state.
- content hash, immutable version record, registry schema v3, and Phase-3
  Project State schema remain unchanged.
- no automatic SUPERSEDED behavior, Task Engine, Agent Engine, cloud AI, or
  UAV execution change is introduced.
- acceptance returns:
  `ARTIFACT_STALE=PASS root=ART-000001@v1 stale=3 unrelated=APPROVED restart=PASS parent_gate=PASS registry_tables=5 project_schema_tables=3 superseded=reserved_for_A4_7`.

A4.7 SUPERSEDED handling remains the sole next step.
