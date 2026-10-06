# Phase 9 / R9.5 Gate — Rework Task

Status in this package: **CANDIDATE** pending target evidence.

R9.5 is GREEN only after the target deployment applies the exact-parent package,
passes the full regression suite, and reports `REWORK_TASK_GENERATION=PASS` from
`tools/check_phase9_rework_task.py`.

R9.5 creates a new persisted owner task from the deterministic R9.4 route. The
new task depends on the review task, consumes the exact reviewed artifact, and
inherits the owner producer execution/output contract. It begins in
`CREATED/attempt=0` and is idempotent across restart.

The review-level max-rework gate remains R9.6. Cross-issue fingerprint duplicate
suppression remains R9.7. CP9 remains pending.
