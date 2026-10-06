# A4.5 — Artifact State Transitions

A4.5 makes the frozen C1.3 artifact state machine executable and persistent.

## Persisted lifecycle

Artifact creation data remains in immutable `artifact_versions.record_json`.
Current lifecycle state is stored separately:

```text
artifact_lifecycle
├── artifact_id
├── version
├── status
└── validation_result_json
```

This keeps content identity, producer, dependency declarations, content hash,
and creation timestamp unchanged while lifecycle state advances.

The Artifact Registry schema moves from v2 to v3:

```text
artifact_dependency_edges
artifact_heads
artifact_lifecycle
artifact_registry_meta
artifact_versions
```

The Phase-3 Project State database remains unchanged.

## Frozen transition graph

A4.5 reuses the C1.3 transition contract exactly:

```text
DRAFT -> REVIEWED
REVIEWED -> VALIDATED
VALIDATED -> APPROVED
```

Legal direct exits from active states to `REJECTED`, `STALE`, and
`SUPERSEDED` remain exactly as defined by C1.3.

Historical states remain terminal for that artifact version.

## Deterministic validation gate

`REVIEWED -> VALIDATED` requires a C1.7 `ValidationResult` with:

```text
overall = PASS
```

A failing deterministic validation cannot produce `VALIDATED`.

A `REVIEWED -> REJECTED` transition may persist a supplied deterministic
`ValidationResult` only when `overall = FAIL`.

`VALIDATED -> APPROVED` reuses the already persisted PASS validation result;
approval cannot replace it.

This layer executes state transitions but does not itself run validators.

## Immutable creation record

State transitions do not modify:

```text
artifact_id
version
type
producer_task
input_dependency_versions
hash
created_timestamp
```

The original `record_json` remains unchanged. `ArtifactVersionStore.open_*()`
overlays the persisted lifecycle state and validation result when returning the
current ArtifactRecord view.

## Migration

A clean A4.3 registry schema v2 is migrated to v3 by adding and backfilling
`artifact_lifecycle` from existing ArtifactRecord state fields.

A4.2 schema-v1 registries can still migrate through the existing dependency-edge
backfill and then lifecycle backfill.

## Still gated

A4.5 deliberately does not automate:

- STALE propagation — A4.6;
- SUPERSEDED handling — A4.7;
- whole-graph stress/consistency tests — A4.8;
- Task Engine or Agent Engine behavior;
- UAV execution.

A4.6 may later call this low-level transition primitive to mark affected
artifact versions STALE.
