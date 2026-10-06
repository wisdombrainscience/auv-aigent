# Phase 9 / R9.7 Gate — Duplicate Issue Suppression

Status in this package: **CANDIDATE** pending target evidence.

R9.7 is GREEN only after the exact-parent target applies this package, passes
the full regression suite, and reports `DUPLICATE_ISSUE_SUPPRESSION=PASS` from
`tools/check_phase9_duplicate_issue_suppression.py`.

Acceptance requires a cross-issue repeated `ReviewIssue.fingerprint` in the same
review lineage to reuse the existing rework task with no new task write and no
additional R9.6 budget consumption. A different fingerprint must remain
independent, the third distinct rework root cause must still stop at typed
`NEEDS_HUMAN`, and suppression must survive restart. Same fingerprint in another
review lineage must not be suppressed.

Passing R9.7 completes **Phase 9 — Review + Feedback**. Per the frozen checkpoint
policy, canonical **CP9 is UAV Bridge stable**, so Phase-9 completion must not be
misreported as CP9 GREEN.
