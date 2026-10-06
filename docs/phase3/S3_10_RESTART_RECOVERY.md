# S3.10 — Restart and Recovery

S3.10 closes Phase 3 by testing and hardening Project State restart behavior.

## Successful restart coverage

A fresh `ProjectStore` must reconstruct and read:

- immutable RequirementSet history from S3.5;
- current FactSet from S3.6;
- current AssumptionSet from S3.7;
- current DecisionSet from S3.8;
- successfully committed S3.9 multi-category state.

## Interrupted transaction recovery

S3.10 extends S3.9 with one bounded prepared recovery journal:

```text
.project-state-transaction.json
```

Before the first visible transaction target is replaced, the journal stores the
pre-commit content (or absence) for only the six approved Project State files:

```text
facts.json
facts.json.sha256
assumptions.json
assumptions.json.sha256
decisions.json
decisions.json.sha256
```

If the process terminates while a commit is visible, the journal remains.
After restart:

```text
recover_project_state_transaction(...)
```

restores the exact pre-commit state and removes the journal durably.

A corrupt, foreign, oversized, duplicated-key, symlinked, or otherwise invalid
journal fails closed. Recovery never guesses around corruption.

## Atomicity boundary

A commit becomes durable only after all target files have been fsynced and the
prepared journal has been removed and the project directory fsynced.

If a crash happens after all target writes but before journal removal, restart
recovery deliberately rolls back to the prior state. This favors consistency
over assuming an unacknowledged commit succeeded.

## Bounds

```text
MAX_TRANSACTION_FILES         = 6
MAX_TRANSACTION_STAGED_BYTES  = 8 MiB
MAX_TRANSACTION_JOURNAL_BYTES = 12 MiB
```

No arbitrary file paths or SQL operations are accepted.

## Preserved boundaries

S3.10 does not modify RequirementSet history or REQ allocation. It does not add
artifacts, tasks, agents, cloud AI, PX4/MAVSDK control, or UAV execution logic.

The S3.1 SQLite schema remains unchanged.

Passing S3.10 closes CP3 — Project Persistence Stable.
