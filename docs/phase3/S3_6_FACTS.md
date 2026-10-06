# S3.6 — Facts

S3.6 adds explicit Project State facts while preserving the frozen provenance rule:

```text
FACT != ASSUMPTION != PROPOSAL
```

A fact has exactly:

```text
key
value
provenance
```

The only allowed fact provenance classes are `USER_FACT` and `VERIFIED_FACT`.
S3.6 never promotes `USER_FACT` to `VERIFIED_FACT`; the classification must be
supplied by an external verification path with appropriate authority.

Fact values are strict finite JSON and are immutable in memory. Fact keys are
stable bounded identifiers such as `vehicle.mass_kg`.

Each project persists one current bounded `FactSet` as:

```text
facts.json
facts.json.sha256
```

The snapshot is hash-checked and restart-readable. S3.6 does not version facts;
only requirements are explicitly versioned by S3.5. It also does not add or
change SQLite tables, does not create assumptions or decisions, and does not
introduce the reusable transaction wrapper reserved for S3.9.

Limits:

```text
MAX_FACTS_PER_SET = 256
MAX_FACT_KEY_CHARS = 128
MAX_FACT_JSON_DEPTH = 16
MAX_FACT_STORE_BYTES = 2 MiB
```
