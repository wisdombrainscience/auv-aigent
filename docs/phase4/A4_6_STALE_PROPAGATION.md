# A4.6 — STALE Propagation

A4.6 implements deterministic lineage invalidation.

The frozen architecture requires:

```text
Requirement v1
      ↓
Mass Budget v2
      ↓
Power v1
      ↓
Battery v1
```

When an upstream dependency is invalidated, affected descendants become:

```text
Mass Budget v2 → STALE
Power v1       → STALE
Battery v1     → STALE
```

Unrelated artifacts remain unchanged.

## Explicit invalidation boundary

A4.6 does not guess whether a newly-created requirement/artifact version
semantically invalidates an old result. Higher deterministic project logic
supplies the exact invalid dependency version.

Two bounded operations are provided:

```text
propagate_dependency_invalidation(project, dependency_version)
mark_artifact_stale(project, artifact_id, version)
```

The first marks direct and transitive artifact dependents of an exact REQ/ART
version. The second marks the root artifact version plus its transitive
dependents.

## Historical states

`REJECTED`, `STALE`, and `SUPERSEDED` are not rewritten. Traversal still
continues through historical nodes so downstream current artifacts cannot
silently survive an invalid ancestor.

## Currentness gate

A4.6 extends the A4.5 APPROVED gate:

```text
required artifact parent = STALE
        ↓
child APPROVED transition = FAIL
```

This enforces the frozen rule that an artifact whose required parent is STALE
cannot be accepted as current.

## Bounds

```text
MAX_STALE_PROPAGATION_ARTIFACTS = 4096
```

Traversal uses bounded recursive SQL and one registry transaction.

## Preserved boundaries

A4.6 does not:

- automatically infer semantic invalidation;
- automatically mark older versions SUPERSEDED — A4.7;
- change Artifact Registry schema v3;
- change Phase-3 Project State schema;
- compute new hashes;
- add Task Engine / Agent Engine behavior;
- modify UAV execution.
