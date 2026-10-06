# V7.10 — Deterministic Validation Issue Schema

## Frozen scope

V7.10 is the final work package in frozen Phase 7 — Deterministic Engineering
Trust. It standardizes deterministic issue objects derived from failed C1.7
`ValidationCheck` records. It does **not** change `ValidationResult`, execute a
new engineering validator, allocate canonical `ISSUE-####` IDs, persist issues,
route owners, generate rework tasks, call an AI provider, or transition artifact
lifecycle state.

This boundary is intentional. The frozen review model reserves canonical issue
allocation, duplicate suppression, routing and rework for later review/rework
phases. V7.10 provides the stable deterministic handoff schema those phases may
consume without mutating Phase-7 validation authority.

## Schema

One normalized validation issue contains exactly:

```json
{
  "fingerprint": "sha256:<64 lowercase hex digits>",
  "check_id": "constraint.twr.min",
  "field": "propulsion.thrust_to_weight",
  "reason": "constraint twr.min failed: observed_value >= required_value is false",
  "details": {
    "observed_value": 2,
    "required_value": 2.5,
    "unit": "1"
  }
}
```

The envelope is:

```json
{
  "issues": []
}
```

A PASS-only `ValidationResult` deterministically maps to an empty issue list.
Only `FAIL` checks produce issues, in the same declared order as C1.7 checks.

## Stable fingerprint

The root-cause fingerprint is:

```text
sha256(canonical_json({check_id, field, reason}))
```

Canonical JSON uses sorted keys, UTF-8, no whitespace and strict finite JSON.
Validator-specific `details` are deliberately excluded from identity. This keeps
the fingerprint stable when repeated validation produces different observed
numeric evidence for the same root cause.

Evidence is still preserved verbatim as an immutable strict-JSON snapshot in
`details`.

## Bounded normalization

V7.10 permits at most 256 failed checks per normalized issue set. Current V7.9
constraint evaluation is already bounded to 64 constraints; the larger V7.10
bound leaves room for future deterministic validators without introducing an
unbounded normalization path.

Duplicate fingerprints inside one issue set are rejected fail-closed.

## Deliberate non-fields

V7.10 validation issues intentionally do not contain:

- `issue_id`;
- `severity`;
- `category`;
- `required_action`;
- owner/routing data;
- task/rework state;
- artifact lifecycle state.

Those belong to later review/rework issue persistence and routing. In
particular, V7.10 never advances the Project State `issue` canonical-ID sequence.

## Authority and lifecycle boundary

The source C1.7 `ValidationResult` remains authoritative for deterministic
engineering checks. V7.10 normalization does not replace or mutate it.

Artifacts remain `DRAFT`, persisted `validation_result` remains null, task state
is unchanged, and the independent UAV verifier remains the sole authoritative
simulation PASS/FAIL authority.
