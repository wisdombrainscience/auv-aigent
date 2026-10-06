# A4.3 — Dependency Edges / Lineage

A4.3 turns the A4.1 `input_dependency_versions` declarations into persisted,
validated lineage edges.

## Registry schema v2

The per-project Artifact Registry evolves from three to four tables:

```text
artifact_dependency_edges
artifact_heads
artifact_registry_meta
artifact_versions
```

The Phase-3 `project_state.sqlite3` remains unchanged.

Existing clean A4.2 registries are migrated in place from schema version 1 to
version 2 by backfilling edges from immutable artifact records.

## Edge identity

Each edge is version-specific:

```text
ART-000010@v2
    ├── REQ-000003@RequirementSet-v4
    └── ART-000004@v1
```

Persisted columns:

```text
child_artifact_id
child_version
ordinal
dependency_kind
dependency_id
dependency_version
```

`ordinal` preserves the declaration order in the immutable ArtifactRecord.

## Target validation

A requirement dependency is valid only when:

1. the referenced RequirementSet version exists; and
2. the referenced REQ ID is present in that version.

An artifact dependency is valid only when that exact artifact version already
exists in the same project registry.

This "target must already exist before child" rule also prevents forward/self
artifact edges. Since new artifact-version nodes only point backward to
already-persisted nodes, A4.3 cannot introduce a cycle during normal writes.

A4.8 remains responsible for the later dedicated graph test package.

## Immutability

Edges are written transactionally with the artifact version that declares them.
There is no public `add_edge` or `remove_edge` API. Read APIs expose:

```text
dependencies(child)
direct_dependents(dependency)
verify_project(project)
```

The registry verifier fails closed if edge rows drift from immutable
ArtifactRecord declarations.

## Still gated

A4.3 does not:

- compute or verify artifact content hashes — A4.4;
- execute lifecycle state transitions — A4.5;
- propagate STALE — A4.6;
- implement SUPERSEDED behavior — A4.7;
- add Task Engine / Agent Engine;
- modify UAV execution.
