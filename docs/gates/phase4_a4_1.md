# Gate — Phase 4 A4.1 Artifact Schema

A4.1 is GREEN only when:

- CP3 is GREEN.
- installer preflight/apply pass.
- full target regression has zero failures.
- canonical ART, project, and producer TASK IDs are enforced.
- positive bounded artifact version is required.
- type and UTC created timestamp are strict and bounded.
- hash field is exactly lowercase SHA-256 text.
- all frozen ArtifactState values remain readable.
- validation_result reuses the deterministic C1.7 contract.
- dependency-version references are bounded and limited to requirement/artifact
  identities.
- strict JSON round-trip preserves every field.
- no ART allocation, registry persistence, version-management semantics,
  dependency graph semantics, hash calculation, state transition execution,
  STALE/SUPERSEDED logic, cloud AI, or UAV execution-plane change is added.
- acceptance returns:
  `ARTIFACT_SCHEMA=PASS fields=10 dependencies=2 status=DRAFT hash=sha256 versioning=reserved_for_A4_2 edges=reserved_for_A4_3`.

A4.2 artifact versions remains the sole next step.
