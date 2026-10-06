# A4.4 — Artifact Hashing

A4.4 makes the A4.1 `hash` field authoritative.

## Hash definition

The artifact hash is:

```text
SHA-256(exact artifact payload bytes)
```

It does not hash lifecycle status, validation result, timestamps, dependency
rows, or other mutable registry metadata.

`ArtifactVersionInput` now carries transient payload `content`. The version
store computes the SHA-256 itself; callers no longer supply a trusted hash.

## Content-addressed filesystem store

Payload bytes are stored outside SQLite:

```text
UAV-PROJ-000001/
└── artifact_content/
    └── sha256/
        └── ab/
            └── <64-hex-sha256>.blob
```

This follows the frozen architecture split:

```text
SQLite     → metadata / state / relationships
filesystem → artifact content
```

The path is derived only from SHA-256. Identical content in the same project is
deduplicated automatically.

## Bounds

```text
MAX_ARTIFACT_CONTENT_BYTES = 64 MiB
ARTIFACT_HASH_CHUNK_BYTES  = 1 MiB
```

Oversized input fails rather than being truncated.

## Integrity behavior

`open_version()` verifies the content blob against the immutable record hash.
Missing, symlinked, or tampered content fails closed.

The registry stores only the digest, not the artifact payload bytes.

## Preserved boundaries

A4.4 does not:

- execute artifact lifecycle transitions — A4.5;
- propagate STALE — A4.6;
- handle SUPERSEDED — A4.7;
- add Task Engine or Agent Engine behavior;
- change the Phase-3 Project State schema;
- modify UAV execution.
