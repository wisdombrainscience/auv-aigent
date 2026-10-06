# R9.1 — Review / Feedback Issue Schema

R9.1 promotes the issue object already frozen inside the C1.6 `ReviewResult`
contract into the standalone Phase-9 review/feedback issue contract. It does
not create a second competing issue representation.

## Wire schema

A review issue contains exactly:

```json
{
  "issue_id": "ISSUE-0041",
  "fingerprint": "energy:capacity:available-vs-required",
  "severity": "HIGH",
  "category": "ENERGY",
  "field": "battery.capacity_wh",
  "reason": "Available energy does not satisfy endurance constraint.",
  "required_action": "Recalculate energy budget."
}
```

`field` may be null. `issue_id` must be a canonical `ISSUE-####` identifier.
`fingerprint` must be non-empty and stable, but R9.1 deliberately does not
invent a fingerprint hashing algorithm; cross-review duplicate suppression is
R9.7. Severity and category vocabularies remain open because the frozen
baseline does not enumerate them.

## Contract relationship

`ReviewIssue` remains the issue type embedded by `ReviewResult`. R9.1 adds an
explicit `REVIEW_ISSUE_CONTRACT_VERSION` and `parse_review_issue()` entrypoint
without changing the C1.6 wire representation.

R9.1 review issues are distinct from V7.10 `ValidationIssue`: validation issues
come deterministically from failed validator checks and have
`fingerprint/check_id/field/reason/details`; review issues are advisory reviewer
findings and additionally carry canonical issue ID, severity, category and
required action.

## Reserved Phase-9 work

R9.1 does not allocate or persist issue IDs, execute the reviewer, generate
review tasks, route an issue to an owner, create rework tasks, enforce rework
limits, or suppress duplicate issues across reviews. These remain R9.2–R9.7.
It also does not call a provider or mutate artifacts/tasks/project state.
