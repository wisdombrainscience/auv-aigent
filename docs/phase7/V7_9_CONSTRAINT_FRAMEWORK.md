# V7.9 — Deterministic Constraint Framework

## Frozen scope

V7.9 adds only the generic deterministic numeric-constraint framework required by frozen Phase 7. It composes the domain calculators/validators implemented in V7.4–V7.8. It does not implement V7.10 validation-issue schema, artifact lifecycle transitions, reviewer policy, orchestration, simulation execution, or UAV verification.

The framework deliberately does **not** hard-code domain policy. Minimum thrust-to-weight, minimum reserve, maximum mass, maximum mission distance, and similar thresholds must be supplied explicitly by the engineering requirement/proposal. V7.9 only evaluates those declared constraints deterministically.

## Constraint calculation

Exactly one `result.calculations` entry is tagged `kind="constraints"`:

```json
{
  "kind": "constraints",
  "constraints": [
    {
      "constraint_id": "twr.min",
      "field": "propulsion.thrust_to_weight",
      "source_kind": "thrust_to_weight",
      "source_field": "reported_thrust_to_weight",
      "operator": ">=",
      "required_value": 1.5,
      "unit": "1"
    }
  ]
}
```

A constraint does not carry an independently declared `observed_value`. Instead, `source_kind` and `source_field` bind the observation to one supported V7.4–V7.8 calculation in the same `ai.agent_result.v1` artifact.

## Supported deterministic sources

V7.9 intentionally supports only the current Phase-7 calculator outputs:

- `mass_budget`: `reported_total_kg`, `limit_kg` (`kg`);
- `battery_energy`: `voltage_v` (`V`), `capacity_ah` (`Ah`), `reported_energy_wh` (`Wh`);
- `reserve`: `reserve_percent` (`%`), `reported_reserve_wh`, `reported_usable_energy_wh` (`Wh`);
- `thrust_to_weight`: `total_thrust_n` (`N`), `mass_kg` (`kg`), `gravity_m_s2` (`m/s^2`), `reported_thrust_to_weight` (`1`);
- `mission_distance`: `reported_distance_m` (`m`).

For every source kind used by a constraint, the producer task must also request that source validator. V7.9 executes the source validator on the same artifact and requires its C1.7 result to PASS before the source scalar may be used. A failed source therefore cannot be hidden behind a passing policy comparison.

## Operators and arithmetic

Operators are exactly:

```text
<  <=  ==  !=  >=  >
```

Observed and required values are compared using `Decimal(str(value))`. Values must be finite JSON numbers and booleans are not numbers. No tolerance, fuzzy comparison, or silent unit conversion exists.

Units are fixed by the selected source field and must use the canonical V7.3 spelling.

## Bounds and stable identity

- constraint count: `1..64`;
- constraint IDs are unique and at most 64 characters;
- IDs use only ASCII letters, digits, `_`, `.`, `-` and begin with an alphanumeric character;
- presentation `field` text is at most 128 characters;
- artifact decode remains bounded by the existing 10 MiB result-artifact limit.

Each valid constraint becomes one C1.7 `ValidationCheck` with stable check ID `constraint.<constraint_id>`. A policy miss is a deterministic engineering FAIL, not an infrastructure conflict.

Malformed framework input returns one `constraints` FAIL check. Unsafe Project/Artifact/Task binding failures remain conflict errors and fail closed.

## Authority boundary

V7.9 returns C1.7 `ValidationResult` only. It does not create V7.10 issues, persist validation evidence, mark an artifact VALIDATED/APPROVED, mutate task state, call Ollama, command PX4/MAVSDK, or create the simulation verdict. The independent UAV verifier remains the sole authority for simulation PASS/FAIL.
