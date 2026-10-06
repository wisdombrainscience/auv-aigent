# Phase 9 / R9.6 Gate — Max-Rework Gate

Status in this package: **CANDIDATE** pending target evidence.

R9.6 is GREEN only after the exact-parent target applies this package, passes
the full regression suite, and reports `MAX_REWORK_GATE=PASS` from
`tools/check_phase9_max_rework_gate.py`.

Acceptance requires the frozen review limit `max_rework_attempts=2`, the first
two R9.5 rework tasks to remain creatable, a third distinct request in the same
review lineage to produce a typed `NEEDS_HUMAN` outcome with no additional task
write, idempotent reuse of existing issues after exhaustion, and restart-stable
behavior. `TaskRecord.max_attempts=3` remains the separate per-task execution
retry budget. Duplicate-fingerprint suppression remains R9.7. CP9 remains
pending.
