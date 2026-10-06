# C1.7 — ValidationResult Contract

Status: candidate for target gate.

## Baseline basis

The frozen architecture names `ValidationResult` as the C1.7 contract and
defines the authority model:

- AI Reviewer: advisory;
- Deterministic Engineering Validator: authoritative for checks it implements;
- UAV Independent Verifier: sole authoritative simulation PASS/FAIL authority.

The baseline does **not** prescribe an exact C1.7 wire-format object.
C1.7 therefore freezes a minimal implementation-level envelope needed by the
later deterministic validators without introducing validator execution early.

## Minimum envelope

```json
{
  "overall": "PASS",
  "checks": [
    {
      "check_id": "mass_budget",
      "status": "PASS",
      "field": "mass.total_kg",
      "reason": null,
      "details": {
        "observed_kg": 4.2,
        "limit_kg": 5.0
      }
    }
  ]
}
```

Allowed statuses are exactly `PASS` and `FAIL`.

The result must contain at least one deterministic check.

`overall` is constrained by the checks:

- all child checks PASS -> overall PASS;
- any child check FAIL -> overall FAIL.

A caller cannot declare PASS while hiding a failed check.

A failed check requires a non-empty reason.

## Details

`details` is a strict-JSON evidence object for validator-specific data such as
observed values, required limits, and units. NaN/Infinity and non-JSON values
are rejected. Nested details are stored as an immutable snapshot.

C1.7 does not define each validator's detailed schema. Phase 7 will own the
mass-budget, battery-energy, reserve, thrust-to-weight, mission-distance,
mission-duration, workspace-bound, unit, finite-number, schema, and constraint
validators listed by the frozen architecture.

## Authority boundary

`ValidationResult` is for deterministic **engineering** validation only.

It does not replace the existing UAV `verification.json`, and it cannot create
a simulation/capability/mission verdict. No AI model field or reviewer approval
field is accepted in this contract.

## Deliberate boundaries

C1.7 does not execute validators, persist results, approve artifacts, trigger
rework, call Ollama, run orchestration, perform RAG, or access PX4/MAVSDK.

Those remain later gated work packages.
