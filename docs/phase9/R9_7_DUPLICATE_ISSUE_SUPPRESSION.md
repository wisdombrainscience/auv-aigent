# R9.7 — Duplicate Issue Suppression

R9.7 closes the Phase-9 review/rework loop by enforcing the frozen requirement
that repeated identical review findings do not create unbounded duplicate tasks.

## Identity and scope

`ReviewIssue.fingerprint` remains the reviewer-supplied stable root-cause key from
C1.6/R9.1. R9.7 does not invent a new semantic fingerprint algorithm. The
existing R9.5 bounded persistence representation is reused:

```text
SHA256(ReviewIssue.fingerprint)
```

stored in the reserved R9.5 rework-task title.

Suppression is scoped to one exact persisted review lineage:

```text
review task + exact reviewed artifact version + routed producer contract
```

The same fingerprint in another review lineage is independent.

## Ordering

Inside the existing R9.5 generation lock:

```text
same canonical issue-id recovery
        ↓
duplicate fingerprint suppression
        ↓
R9.6 max-rework gate
        ↓
create new rework task
```

This ordering is intentional. A repeated root cause reuses its already-persisted
rework task and therefore does not consume another `max_rework_attempts=2` slot.
A genuinely different fingerprint remains independent and is still subject to
R9.6.

## Fail-closed boundaries

- Reusing the same canonical `ISSUE-####` with a changed fingerprint remains a
  conflict from R9.5.
- Malformed reserved R9.5 task titles remain fail-closed.
- The suppressor performs no task/artifact/issue writes by itself.
- No issue database, provider call, model call, task execution, or lifecycle
  transition is introduced.
- Legacy pre-R9.7 duplicate tasks are not deleted; the lowest canonical TASK ID
  is deterministically reused and no additional duplicate is written.

## Phase boundary

R9.7 is the last work package in Phase 9. Passing its target regression and
acceptance evidence completes the frozen **Review + Feedback** phase. It does
not, by itself, satisfy canonical checkpoint CP9, which the frozen baseline
names **UAV Bridge stable** and is reached only after the later UAV Bridge work.
