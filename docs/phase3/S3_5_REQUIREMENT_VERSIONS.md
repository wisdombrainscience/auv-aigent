# S3.5 — Requirement Versions

S3.5 implements the frozen rule:

```text
RequirementSet v1
    ↓
RequirementSet v2
    ↓
...
```

with all old versions remaining readable.

## Persistent representation

Each project uses its existing `requirements/` directory:

```text
requirements/
├── current.json
├── v000001.json
├── v000001.json.sha256
├── v000002.json
└── v000002.json.sha256
```

Snapshots and their SHA-256 sidecars are immutable. `current.json` is a bounded
pointer to the latest version.

The existing SQLite `canonical_id_sequences` table remains authoritative for
persistent `REQ-*` allocation. S3.5 does not change the S3.1 SQLite schema.

## Version semantics

Version 1 has `parent_version=null`. Every later version points to exactly the
immediately previous version. Branching is not introduced in S3.5.

A full RequirementSet snapshot is written for every version. Requirement order
and USER_REQUIREMENT provenance come from the S3.4 contract.

New requirement entries use `requirement_id=None` and receive the next global
canonical REQ ID. A supplied requirement ID must occur in the immediately
previous version of the same project. This prevents bypassing persistent ID
allocation or importing an ID from another project.

Removed requirements simply do not appear in the next full snapshot. Old
versions remain readable.

## Status

The S3.4 `DRAFT` / `APPROVED` status is persisted with each snapshot. S3.5 does
not introduce approval authority or a separate approval workflow.

## Operation-local atomicity

Create/append uses one bounded `BEGIN IMMEDIATE` operation so REQ-ID allocation
and filesystem snapshot creation either complete together or are rolled back.

This is deliberately operation-specific. The reusable Project State transaction
wrapper remains S3.9.

## Still gated

S3.5 does not implement:

- ChangeRequest objects;
- dependency impact analysis;
- artifact STALE propagation;
- facts, assumptions, or decisions;
- Agent Engine or Orchestrator behavior;
- cloud AI or UAV execution-plane changes.
