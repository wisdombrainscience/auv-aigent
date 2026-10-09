# F12.2 — Simulation Issue Taxonomy

F12.2 deterministically converts the failed checks of one F12.1
`ParsedVerifierResult` into bounded simulation-feedback issues.

## Scope

This gate performs classification only. It does not allocate canonical ISSUE IDs,
choose engineering roles, invalidate artifacts, create rework tasks, authorize a
simulation retry, or transition a task/project to `NEEDS_HUMAN`.

The existing C1.8 failure taxonomy remains authoritative. F12.2 therefore embeds
`FailureDescriptor` and uses only `FailureClass.SIMULATION` or
`FailureClass.VERIFICATION`; it does not create a parallel failure-class system.

## Canonical mapping

| Verifier check | Issue type | Failure class | Failure code |
|---|---|---|---|
| `step_sequence` | `MISSION_SEQUENCE` | `VERIFICATION` | `VERIFICATION_MISSION_SEQUENCE` |
| `child_verdicts` | `CAPABILITY_VERDICT` | `VERIFICATION` | `VERIFICATION_CHILD_VERDICT` |
| `child_evidence_integrity` | `EVIDENCE_INTEGRITY` | `VERIFICATION` | `VERIFICATION_CHILD_EVIDENCE_INTEGRITY` |
| `continuity` | `NAVIGATION` | `SIMULATION` | `SIMULATION_NAVIGATION_CONTINUITY` |
| `terminal_land_step` | `MISSION_TERMINATION` | `VERIFICATION` | `VERIFICATION_TERMINAL_LAND_STEP` |
| `terminal_landed_state` | `LANDING` | `SIMULATION` | `SIMULATION_TERMINAL_LANDED_STATE` |
| `terminal_home` | `RETURN_HOME` | `SIMULATION` | `SIMULATION_TERMINAL_HOME_POSITION` |

A verifier PASS produces an empty issue set. A FAIL produces exactly one issue per
failed canonical verifier check, in canonical verifier-check order.

## Root-cause fingerprint

The fingerprint is SHA-256 over only `check_id`, `issue_type`, and failure code.
Observed metrics, run ID, failed steps, and free-form verifier reason are excluded
so repeated observations of the same classified root cause remain stable for later
bounded feedback logic.

## Authority

The independent mission verifier remains the verdict authority. F12.2 never
changes PASS/FAIL and preserves `VERIFIER_RESULT` provenance and exact U11.7/F12.1
lineage.
