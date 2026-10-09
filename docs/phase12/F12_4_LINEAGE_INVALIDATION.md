# F12.4 — Lineage Invalidation

## Purpose

F12.4 converts a routed simulation failure into deterministic artifact-lineage invalidation.

## Rule

A PASS verifier result produces an empty F12.3 route plan and causes no mutation.

For a FAIL route plan, every exact `source_artifacts` version bound to the UAV run is an invalidation root. F12.4 reuses the existing A4.6 `ArtifactStaleStore.mark_artifact_stale()` operation, so each root and all direct/transitive artifact descendants become `STALE` when their lifecycle permits it. Historical `REJECTED`, `STALE`, and `SUPERSEDED` versions are not rewritten, while traversal still continues through them.

The approved requirement version is preserved; F12.4 does not invalidate requirements.

## Safety boundary

All source roots are validated for existence and project ownership before the first lifecycle mutation. Roots are bounded by the existing simulation proposal maximum of 64.

No new database/schema is introduced. Persistence is the existing artifact lifecycle in the Artifact Registry.

## Preserved feedback lineage

The result preserves project ID, requirement version, exact source artifact lineage, runtime run ID, mission/scenario IDs, verifier evidence SHA-256, issue fingerprints, and `VERIFIER_RESULT` provenance.

## Not in F12.4

- ISSUE ID allocation/persistence;
- rework task generation — F12.5;
- bounded simulation retry — F12.6;
- `NEEDS_HUMAN` transition — F12.7;
- provider/model calls;
- PX4/MAVSDK access;
- verifier verdict reinterpretation.
