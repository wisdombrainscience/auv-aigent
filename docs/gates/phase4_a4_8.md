# Gate — Phase 4 A4.8 Graph Tests

A4.8 is GREEN only when:

- A4.7 is GREEN.
- installer preflight/apply pass.
- full target regression has zero failures.
- chain lineage remains exact across restart.
- diamond/fan-out/fan-in graphs do not duplicate STALE propagation.
- requirement-version invalidation reaches all dependent descendants and leaves unrelated artifacts unchanged.
- dependency references remain version-specific.
- forward/current-self and cross-project artifact edges fail closed.
- stale/superseded required parents cannot silently be accepted as current.
- supersession currentness and replacement history remain deterministic.
- explicit invalidation can stale downstream artifacts that still reference a superseded historical version.
- edge tampering fails closed against immutable ArtifactRecord declarations.
- graph/lifecycle operations do not alter content hashes or payload bytes.
- Artifact Registry remains schema v4 with 6 tables.
- Phase-3 Project State remains exactly 3 tables.
- no Task Engine, Agent Engine, cloud AI, or UAV execution change is introduced.
- acceptance returns `ARTIFACT_GRAPH=PASS ... cp4=READY`.

Passing this gate closes CP4 — Artifact Lineage Stable.
