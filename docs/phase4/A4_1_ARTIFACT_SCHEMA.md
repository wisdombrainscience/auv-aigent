# A4.1 — Artifact Schema

A4.1 is the first work package after CP3.

The frozen baseline requires every important artifact to carry:

```text
artifact_id
version
type
producer task
input dependency versions
hash
status
validation result
created timestamp
```

A4.1 freezes those fields and adds `project_id` so an Artifact Registry record
is explicitly scoped to one Project State.

## Exact record

```text
artifact_id
project_id
version
type
producer_task
input_dependency_versions
hash
status
validation_result
created_timestamp
```

Identity contracts reuse Phase-1 canonical IDs:

```text
artifact_id   ART-xxxxxx
project_id    UAV-PROJ-xxxxxx
producer_task TASK-xxxxxx
```

`status` reuses the frozen Phase-1 `ArtifactState` lifecycle.

`validation_result` is the existing deterministic C1.7 `ValidationResult`, or
null before deterministic validation exists.

`hash` must already be a lowercase SHA-256 digest. A4.1 validates the wire
field but deliberately does not calculate it; hashing is A4.4.

`created_timestamp` is canonical UTC RFC3339 text ending in `Z`.

## Dependency-version references

A schema-level input dependency may refer to:

```text
REQ-xxxxxx @ version
ART-xxxxxx @ version
```

A4.1 only records these declarations. It does not check graph existence,
cycles, reachability, STALE propagation, or currentness. Those semantics belong
to A4.3 and later.

## Explicit boundaries

A4.1 does not:

- allocate ART IDs;
- persist an Artifact Registry;
- create new artifact versions;
- compute content hashes;
- execute artifact state transitions;
- create dependency edges/graph traversal;
- propagate STALE;
- handle SUPERSEDED replacement;
- modify Project State SQLite schema;
- add Task Engine or Agent Engine behavior.

The next work package is A4.2 artifact versions.
