# A4.2 — Artifact Versions

A4.2 adds persistent logical artifact identity and append-only artifact content
versions on top of the A4.1 schema.

## Registry storage

Each project receives a local registry database:

```text
UAV-PROJ-000001/
└── artifact_registry.sqlite3
```

This is deliberately separate from the Phase-3 `project_state.sqlite3`, so the
CP3 Project State schema remains unchanged.

A4.2 registry schema:

```text
artifact_registry_meta
artifact_versions
artifact_heads
```

`artifact_versions` stores canonical A4.1 record JSON for every version.
`artifact_heads` stores only the latest version number for each logical
artifact.

## Identity and version semantics

Creating a new logical artifact:

```text
canonical_id_sequences[artifact] -> ART-000001
ART-000001 version 1
status = DRAFT
validation_result = null
```

Artifact IDs use the existing persistent Phase-3 canonical ID sequence, so they
remain globally unique across projects.

Appending:

```text
ART-000001 v1
      ↓
ART-000001 v2
      ↓
ART-000001 v3
```

There is no branching in A4.2. The new version must be exactly
`latest_version + 1`.

Old versions remain readable after new versions exist.

The logical artifact `type` is stable across versions. Producer task, dependency
version declarations, hash field, and timestamp may change.

Every newly-created version enters as `DRAFT` with
`validation_result = null`. State transitions are not executed by A4.2.

## Preserved boundaries

A4.2 does not:

- validate dependency existence or cycles — A4.3;
- compute or verify artifact content hashes — A4.4;
- execute lifecycle state transitions — A4.5;
- propagate STALE;
- mark older versions SUPERSEDED;
- add Task Engine or Agent Engine behavior;
- modify PX4/MAVSDK/Gazebo behavior.

Dependency references are stored exactly as A4.1 declarations, even if they do
not yet resolve. A4.3 is the authority that will turn those declarations into a
validated lineage graph.
