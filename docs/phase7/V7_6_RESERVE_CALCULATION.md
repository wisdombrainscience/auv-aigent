# V7.6 — Deterministic Reserve Calculation

Status: candidate pending target validation.

## Frozen scope

V7.6 implements only deterministic nominal energy-reserve bookkeeping needed by
the current engineering workflow. It does not implement thrust-to-weight
(V7.7), mission distance (V7.8), constraints (V7.9), validation-issue schema
(V7.10), endurance, or battery electrochemistry.

The frozen architecture requires engineering quantities that can be checked
reliably with code to be checked deterministically. V7.6 therefore derives
reserve and usable nominal watt-hours from one already-reconciled V7.5 battery
energy calculation and one declared reserve percentage.

## Current input contract

V7.6 operates on the current G6.8 `ai.agent_result.v1` artifact. Exactly one
V7.5 battery calculation must exist:

```json
{
  "kind": "battery_energy",
  "voltage_v": 22.2,
  "capacity_ah": 4.5,
  "reported_energy_wh": 99.9
}
```

and exactly one reserve calculation:

```json
{
  "kind": "reserve",
  "reserve_percent": 20,
  "reported_reserve_wh": 19.98,
  "reported_usable_energy_wh": 79.92
}
```

Canonical units are `Wh` and `%`. No unit conversion occurs.

## Deterministic rules

1. The producer task must request validator `reserve`.
2. The artifact must be the current `ai.agent_result.v1` payload and retain its
   project/task/role/output-schema binding.
3. Exactly one `kind=battery_energy` and exactly one `kind=reserve` calculation
   must exist.
4. The battery calculation must satisfy the V7.5 arithmetic relation
   `reported_energy_wh = voltage_v * capacity_ah` before reserve math is used.
5. `reserve_percent` is a finite number in `0..100` inclusive.
6. Reported reserve and usable energy are finite non-negative numbers.
7. Decimal bookkeeping recomputes:
   `computed_reserve_wh = battery_energy_wh * reserve_percent / 100`.
8. Decimal bookkeeping recomputes:
   `computed_usable_energy_wh = battery_energy_wh - computed_reserve_wh`.
9. Both reported values must equal the deterministic results exactly.

V7.6 treats 0% and 100% as valid bookkeeping boundaries. Whether a selected
reserve percentage is operationally sufficient belongs to V7.9 constraint
policy, not this arithmetic-reconciliation gate.

## Output

V7.6 returns the frozen C1.7 `ValidationResult` with check ID `reserve`.
Evidence includes battery energy, reserve percentage, computed/reported reserve,
computed/reported usable energy, formulas, canonical units, and reconciliation
flags.

## Boundaries

V7.6 does **not**:

- estimate flight endurance;
- model cell chemistry, voltage sag, discharge curves, temperature, or C-rate;
- decide whether a reserve policy is sufficient for a mission;
- perform thrust-to-weight or mission-distance calculations;
- persist `validation_result` into the artifact;
- transition DRAFT/REVIEWED/VALIDATED/APPROVED;
- call an AI provider;
- modify task state;
- convert units;
- perform PX4, MAVSDK, Gazebo, or UAV-verifier work.

Artifact lifecycle therefore remains `DRAFT` and `validation_result` remains
null after this gate.
