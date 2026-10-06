# A4.7 — SUPERSEDED Handling

A4.7 makes logical artifact replacement deterministic.

A newer artifact version does **not** replace the current approved version merely
because it exists. Replacement occurs only when the newer version itself reaches
`APPROVED` through the A4.5 deterministic lifecycle gate.

```text
ART-000001 v1  APPROVED
ART-000001 v2  DRAFT
        ↓ v2 passes review + validation
ART-000001 v1  SUPERSEDED
ART-000001 v2  APPROVED
```

## Replacement metadata

Artifact Registry schema moves from v3 to v4 and adds:

```text
artifact_supersession
├── artifact_id
├── superseded_version
└── replacement_version
```

Rows always point forward in version number. Automatic replacement is recorded in
the same SQLite transaction as the newer version's `APPROVED` transition.

Historical lower versions (`REJECTED`, `STALE`, `SUPERSEDED`) are never rewritten.
Active lower versions (`DRAFT`, `REVIEWED`, `VALIDATED`, `APPROVED`) become
`SUPERSEDED` when a newer version is approved.

## Current approved version

For one logical `ART-*`, normal A4.7 operation permits at most one lifecycle row
in `APPROVED`. A later draft does not displace it. When the later version becomes
approved, currentness moves atomically.

Manual low-level `-> SUPERSEDED` transitions remain legal because they are part
of the frozen C1.3 transition graph. Such legacy/manual transitions need not have
a replacement row; `artifact_supersession` records deterministic automatic
replacement events introduced by A4.7.

## Dependency boundary

A required artifact dependency already in `STALE` or `SUPERSEDED` cannot be used
to approve an unrelated current artifact. The one deliberate exception is a new
version depending on an earlier version of the **same logical artifact** as its
predecessor; that predecessor is superseded only after the replacement passes the
approval gate.

A4.7 does not infer that every replacement semantically invalidates all downstream
artifacts. Explicit semantic invalidation and STALE propagation remain the A4.6
operation. This avoids conflating version replacement with domain-level design
change.

## Preserved boundaries

A4.7 does not add Task Engine, Agent Engine, cloud AI, or UAV execution behavior.
The Phase-3 Project State schema remains unchanged. A4.8 owns the final bounded
whole-graph consistency/stress tests for CP4.
