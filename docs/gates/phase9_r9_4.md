# Phase 9 / R9.4 Gate — Issue → Owner Routing

Status in this package: **CANDIDATE** pending target evidence.

R9.4 is GREEN only after the target deployment applies the exact-parent package,
passes the full regression suite, and reports `ISSUE_OWNER_ROUTING=PASS` from
`tools/check_phase9_issue_owner_routing.py`.

The gate is intentionally read-only. Owner identity is derived from the R9.3
review task's exact artifact input, then `ArtifactRecord.producer_task`, then the
producer `TaskRecord.role`. Review-issue semantic text never selects an owner.

R9.5–R9.7 remain deferred. CP9 remains pending.
