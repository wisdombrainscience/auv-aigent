# V7.4 — Deterministic Mass Budget

Status: candidate pending target validation.

## Frozen scope

V7.4 is the first domain calculator in Phase 7. It implements only deterministic
mass-budget bookkeeping needed by the current local-AI engineering workflow.
It does not implement battery energy (V7.5), reserve (V7.6), thrust-to-weight
(V7.7), mission distance (V7.8), constraints (V7.9), or validation-issue
persistence/schema (V7.10).

The frozen architecture requires that engineering facts which can be checked by
code are checked deterministically. C1.7 explicitly uses mass budget as an
example validation check with observed and limit values.

## Current input contract

V7.4 operates on the current G6.8 `ai.agent_result.v1` artifact. Exactly one
entry of `result.calculations` must be tagged:

```json
{
  "kind": "mass_budget",
  "components": [
    {"name": "airframe", "mass_kg": 1.8},
    {"name": "battery", "mass_kg": 1.1},
    {"name": "payload", "mass_kg": 0.6}
  ],
  "reported_total_kg": 3.5,
  "limit_kg": 4.0
}
```

This is deliberately small. V7.4 does not invent a general aircraft component
ontology. Component names are opaque stable labels local to the calculation.
Mass is canonical kilograms, reusing the V7.3 `_kg` convention. No unit
conversion occurs.

## Deterministic rules

1. The producer task must request validator `mass_budget`.
2. The artifact must be the current `ai.agent_result.v1` payload and retain its
   project/task/role/output-schema binding.
3. Exactly one `kind=mass_budget` calculation must exist.
4. `components` contains 1..128 entries.
5. Every component has exactly `name,mass_kg`; names are clean and unique.
6. Component mass, reported total and limit are finite non-negative numbers.
7. Component masses are summed using decimal representations of the JSON
   numbers, avoiding binary floating-point bookkeeping drift.
8. `reported_total_kg` must equal the deterministic component sum exactly.
9. The computed total must be `<= limit_kg`.

A reconciliation mismatch fails before a limit verdict because the declared
budget total is not trustworthy until it matches the deterministic sum.

## Output

V7.4 returns the frozen C1.7 `ValidationResult` with check ID `mass_budget`.
Evidence includes:

- component count;
- computed total kg;
- reported total kg;
- limit kg;
- reconciliation flag;
- within-limit flag.

## Boundaries

V7.4 does **not**:

- persist `validation_result` into the artifact;
- transition DRAFT/REVIEWED/VALIDATED/APPROVED;
- call an AI provider;
- modify task state;
- convert units;
- perform battery, reserve, propulsion, mission, PX4, MAVSDK, Gazebo, or UAV
  verifier work.

Artifact lifecycle therefore remains `DRAFT` and `validation_result` remains
null after this gate.
