# Gate — Phase 4 A4.4 Hashing

A4.4 is GREEN only when:

- A4.3 is GREEN.
- installer preflight/apply pass.
- full target regression has zero failures.
- ArtifactVersionStore computes SHA-256 from exact payload bytes.
- callers cannot select the persisted hash independently of content.
- content is persisted on filesystem, not embedded in registry JSON.
- content paths are digest-derived and identical payloads deduplicate.
- bounded content and streaming verification limits are enforced.
- restart/open verifies the same content hash.
- tampered, missing, or symlinked blobs fail closed.
- A4.3 lineage behavior remains intact.
- Phase-3 SQLite schema remains unchanged.
- no lifecycle transition, STALE, SUPERSEDED, Task Engine, Agent Engine,
  cloud-AI, or UAV execution change is introduced.
- acceptance returns:
  `ARTIFACT_HASHING=PASS artifact=ART-000001 algorithm=sha256 computed=PASS content_store=PASS dedup=PASS restart=PASS tamper=fail_closed states=reserved_for_A4_5`.

A4.5 state transitions remains the sole next step.
