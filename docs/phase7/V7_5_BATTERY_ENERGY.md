# V7.5 — Deterministic Battery Energy

Status: candidate pending target validation.

## Frozen scope

V7.5 implements only deterministic nominal battery-energy bookkeeping needed by
the current engineering workflow. It does not implement reserve (V7.6),
thrust-to-weight (V7.7), mission distance (V7.8), constraints (V7.9), or
validation-issue persistence/schema (V7.10).

The frozen architecture requires engineering quantities that can be checked
reliably with code to be checked deterministically. V7.5 therefore recomputes
nominal watt-hours from canonical voltage and ampere-hour capacity.

## Current input contract

V7.5 operates on the current G6.8 `ai.agent_result.v1` artifact. Exactly one
entry of `result.calculations` must be tagged:

```json
{
  "kind": "battery_energy",
  "voltage_v": 22.2,
  "capacity_ah": 4.5,
  "reported_energy_wh": 99.9
}
```

Units are the V7.3 canonical spellings `V`, `Ah`, and `Wh`, encoded in the
field suffixes. No unit conversion occurs.

## Deterministic rules

1. The producer task must request validator `battery_energy`.
2. The artifact must be the current `ai.agent_result.v1` payload and retain its
   project/task/role/output-schema binding.
3. Exactly one `kind=battery_energy` calculation must exist.
4. The calculation contains exactly `kind,voltage_v,capacity_ah,reported_energy_wh`.
5. Voltage, capacity, and reported energy are finite non-negative numbers.
6. Energy is recomputed using decimal representations of JSON numbers:
   `computed_energy_wh = voltage_v * capacity_ah`.
7. `reported_energy_wh` must equal the deterministic product exactly.

V7.5 treats zero values as valid bookkeeping inputs. Whether a battery has
sufficient usable energy belongs to V7.6 reserve/constraint checks, not this
formula-reconciliation gate.

## Output

V7.5 returns the frozen C1.7 `ValidationResult` with check ID `battery_energy`.
Evidence includes voltage, capacity, computed/reported energy, canonical units,
formula ID, and reconciliation flag.

## Boundaries

V7.5 does **not**:

- model cell chemistry, voltage sag, discharge curves, C-rate, temperature, or
  pack topology;
- calculate usable reserve or endurance;
- persist `validation_result` into the artifact;
- transition DRAFT/REVIEWED/VALIDATED/APPROVED;
- call an AI provider;
- modify task state;
- convert units;
- perform propulsion, mission, PX4, MAVSDK, Gazebo, or UAV-verifier work.

Artifact lifecycle therefore remains `DRAFT` and `validation_result` remains
null after this gate.
