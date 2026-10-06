# R9.4 — Issue → Owner Routing

## Scope

R9.4 adds deterministic, read-only routing from one `ReviewIssue` to the owner
of the artifact under review. It does not infer an owner from AI-authored
`severity`, `category`, `field`, `reason`, or `required_action` text.

The only routing chain is persisted provenance:

```text
ReviewIssue
    +
R9.3 reviewer TaskRecord
    ↓
exact input ART-######@vN
    ↓
ArtifactRecord.producer_task
    ↓
producer TaskRecord.role
    ↓
IssueOwnerRoute(owner_task_id, owner_role)
```

The review task must retain the R9.3 shape: role `reviewer`, output schema
`review_result.v1`, exactly one artifact input, exactly one producer dependency,
no validators, no recursive review, the frozen retry budget, and the deterministic
R9.3 title. The review dependency must equal `ArtifactRecord.producer_task`.
The producer role must be enabled by the frozen MVP `RoleRegistry`.

## Authority boundary

`IssueOwnerRouter` returns an immutable routing decision only. R9.4 does not
allocate or persist issues, create tasks, transition artifacts, execute a
reviewer, invoke a provider, or mutate Project/Task/Artifact state.

Deferred work remains:

- R9.5: rework task creation.
- R9.6: maximum rework gate.
- R9.7: duplicate issue suppression.

## Acceptance

Run:

```bash
python3 tools/check_phase9_issue_owner_routing.py
```

The probe must report `ISSUE_OWNER_ROUTING=PASS` and no writes/provider/task
execution.
