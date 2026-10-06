# S3.9 — Project State Transaction Wrapper

S3.9 adds the reusable bounded transaction wrapper reserved throughout Phase 3.

## Scope

The wrapper composes the three current snapshot categories already implemented:

```text
FactSet
AssumptionSet
DecisionSet
```

Usage:

```text
ProjectStateTransaction(project, project_id)
    ├── stage_facts(...)
    ├── stage_assumptions(...)
    ├── stage_decisions(...)
    └── commit()
```

A visible write failure rolls back every file already replaced during that
commit. Successful commits survive a normal reopen/restart.

## Deliberate boundary around RequirementSet history

S3.5 requirement versions are immutable append history with persistent REQ-ID
allocation in SQLite. S3.9 does not rewrite those snapshots, expose arbitrary
SQL, or stage a requirement version through a second competing implementation.

This keeps the already-verified S3.5 version/ID authority intact. Future higher
layers may coordinate their operations without silently weakening immutable
requirement history.

## Bounded surface

```text
MAX_TRANSACTION_CATEGORIES   = 3
MAX_TRANSACTION_FILES        = 6
MAX_TRANSACTION_STAGED_BYTES = 8 MiB
```

The transaction does not expose arbitrary file replacement or raw SQL execution.

## State machine

```text
OPEN ──commit──> COMMITTED
  │
  └─rollback──> ROLLED_BACK
```

A transaction cannot be reused after COMMITTED or ROLLED_BACK. Empty commits
fail closed.

## Crash/restart boundary

S3.9 provides in-process all-or-nothing rollback plus successful restart
persistence. Power-loss/crash recovery testing is intentionally the next
roadmap gate, S3.10.

S3.9 does not add tasks, artifacts, lineage, agents, cloud providers, or UAV
execution behavior.
