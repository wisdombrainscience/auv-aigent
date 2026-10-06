# S3.8 — Decisions

S3.8 adds the final basic Project State category before the general transaction
wrapper:

```text
Facts / Assumptions / Decisions
```

## Decision contract

A Decision has exactly:

```text
key
value
rationale
```

`value` is bounded strict finite JSON. `rationale` is required, bounded text.

S3.8 deliberately does **not** add a `DECISION` provenance class because the
frozen provenance taxonomy contains no such value. A decision is a separate
Project State category; it is not silently reclassified as a fact, assumption,
or AI proposal.

S3.8 also does not invent approval authority. The persistence layer records a
decision supplied by an authorized higher layer, but does not decide whether an
AI agent, user, reviewer, or future workflow is permitted to make that choice.

## Persistence

Each project may have one current snapshot:

```text
decisions.json
decisions.json.sha256
```

The snapshot is hash checked, symlink protected, duplicate-key checked, bounded,
and operation-locally restored if the paired write fails.

The S3.1 SQLite schema remains unchanged.

## Limits

```text
MAX_DECISIONS_PER_SET        = 256
MAX_DECISION_KEY_CHARS       = 128
MAX_DECISION_RATIONALE_BYTES = 16384
MAX_DECISION_JSON_DEPTH      = 16
MAX_DECISION_STORE_BYTES     = 2 MiB
```

## Still gated

S3.8 does not implement:

- decision approval/rejection workflow;
- decision IDs or decision version history;
- artifact lineage or STALE propagation;
- general Project State transaction composition;
- Agent Engine or Orchestrator;
- cloud AI or UAV execution-plane changes.

S3.9 is the next step and owns the reusable transaction wrapper.
