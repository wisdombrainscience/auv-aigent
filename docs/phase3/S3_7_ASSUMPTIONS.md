# S3.7 — Engineering Assumptions

S3.7 freezes the Project State representation for explicit engineering
assumptions.

The frozen architecture rule remains:

```text
FACT != ASSUMPTION != PROPOSAL
```

## Assumption contract

Exact fields:

```text
key
value
provenance
```

The only accepted provenance is:

```text
ENGINEERING_ASSUMPTION
```

A stored assumption never becomes `USER_FACT`, `VERIFIED_FACT`, or
`AI_PROPOSAL` simply because it is persisted or repeated.

Values use bounded strict finite JSON. Duplicate keys within one AssumptionSet
fail closed.

## Persistence

Each project may have one current snapshot:

```text
assumptions.json
assumptions.json.sha256
```

The snapshot is hash checked on open and can be replaced atomically. S3.7 does
not introduce assumption version history because the frozen Phase-3 roadmap
does not specify an assumption-version stage.

The S3.1 SQLite schema remains unchanged.

## Limits

```text
MAX_ASSUMPTIONS_PER_SET   = 256
MAX_ASSUMPTION_KEY_CHARS  = 128
MAX_ASSUMPTION_JSON_DEPTH = 16
MAX_ASSUMPTION_STORE_BYTES = 2 MiB
```

Inputs exceeding these bounds fail rather than being truncated.

## Still gated

S3.7 does not implement:

- promotion of assumptions to facts;
- fact verification authority;
- decisions;
- general Project State transaction API;
- Agent Engine or Orchestrator;
- cloud AI or UAV execution-plane changes.

S3.8 decisions is the only next roadmap step after this gate.
