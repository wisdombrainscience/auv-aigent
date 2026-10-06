# S3.4 — RequirementSet Contract

Status: candidate for target gate.

The frozen V8 requirement model says:

```text
Raw User Input
     ↓
RequirementSet v1 DRAFT
     ↓
APPROVED
```

and requires user requirements to remain distinguishable from facts,
assumptions, and AI proposals.

S3.4 freezes only the **content contract** that S3.5 will version.

## Requirement

Exact fields:

```text
requirement_id
text
provenance
```

`requirement_id` must be a canonical `REQ-xxxxxx` identifier.

`provenance` must be exactly:

```text
USER_REQUIREMENT
```

S3.4 never silently converts a user requirement into `USER_FACT`,
`ENGINEERING_ASSUMPTION`, or `AI_PROPOSAL`.

## RequirementSet

Exact fields:

```text
project_id
status
requirements
```

Allowed status values:

```text
DRAFT
APPROVED
```

The requirement order is preserved and duplicate requirement IDs fail closed.

Safety bounds:

```text
MAX_REQUIREMENTS_PER_SET = 256
MAX_REQUIREMENT_TEXT_BYTES = 16384
```

Inputs exceeding these limits fail; they are never truncated.

## Critical S3.4/S3.5 boundary

S3.4 intentionally has **no requirement version field and no persistence API**.

It does not:

- allocate requirement IDs;
- create RequirementSet versions;
- overwrite old requirements;
- write files under `requirements/`;
- add SQLite requirement tables;
- perform change-request impact analysis;
- mark artifacts STALE;
- grant approval authority.

S3.5 owns version identity/history/persistence. This prevents S3.4 from
introducing a temporary non-versioned Project State format that would conflict
with the frozen rule that old requirement versions remain readable.
