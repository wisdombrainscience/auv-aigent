# R9.5 — Rework Task

R9.5 materializes one deterministic rework `TaskRecord` from one `ReviewIssue`
after R9.4 has resolved the artifact producer that owns the finding.

## Persisted shape

The rework task is a **new task**. R9.5 does not reopen or mutate the original
producer task.

```text
ReviewIssue + R9.3 review task
            ↓
R9.4 IssueOwnerRoute
            ↓
R9.5 new TaskRecord
```

The new record:

- uses the routed producer role;
- depends on the review task;
- consumes exactly the reviewed artifact version;
- inherits the producer priority, output schema, validators, review requirement,
  and bounded task-attempt budget;
- starts in `CREATED` with `attempt=0`.

This preserves the distinction between the Phase-5 per-task execution retry
budget (`max_attempts`) and the Phase-9 review rework-count budget. R9.5 does
**not** enforce `review.max_rework_attempts`; that is R9.6.

## Issue identity and restart safety

The task title contains the canonical `ISSUE-####` plus SHA-256 of the already
provided review fingerprint and the exact artifact version. The semantic review
text (`reason`, `required_action`, severity/category/field) is not copied into
persistent task metadata. Repeating the same issue therefore returns the same
persisted task after restart.

Different issue IDs with the same fingerprint are deliberately not suppressed
in R9.5. Cross-issue fingerprint suppression remains R9.7.

## Authority boundary

R9.5 does not execute the task, allocate or persist a standalone issue record,
transition an artifact, enforce the review rework limit, suppress duplicate
issues, call an AI provider, or access PX4/MAVSDK/Gazebo.
