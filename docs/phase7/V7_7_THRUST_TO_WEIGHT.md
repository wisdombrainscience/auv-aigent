# V7.7 — Deterministic Thrust-to-Weight

## Scope

V7.7 implements only the deterministic thrust-to-weight bookkeeping required by
frozen Phase 7. It operates on the current G6.8 `ai.agent_result.v1` artifact
and returns the frozen C1.7 `ValidationResult` contract.

It does not implement V7.8 mission distance, V7.9 constraints, V7.10 validation
issue schema, lifecycle transitions, provider calls, or UAV control.

## Current input contract

Exactly one calculation is tagged:

```json
{
  "kind": "thrust_to_weight",
  "total_thrust_n": 68.64655,
  "mass_kg": 3.5,
  "gravity_m_s2": 9.80665,
  "reported_thrust_to_weight": 2
}
```

The canonical units are inherited from V7.3: newtons, kilograms, metres per
second squared, and dimensionless ratio `1`. No unit conversion occurs.

## Deterministic rule

Weight is derived as:

```text
weight_n = mass_kg * gravity_m_s2
```

The engineering relation is:

```text
T/W = total_thrust_n / weight_n
```

PASS/FAIL reconciliation deliberately uses exact Decimal cross-multiplication:

```text
reported_thrust_to_weight * weight_n == total_thrust_n
```

This avoids introducing an arbitrary floating-point or ratio-rounding policy.
The divided ratio is emitted only as evidence after reconciliation.

Mass and gravity must be strictly greater than zero. Thrust and reported ratio
must be finite and non-negative. Exactly one `thrust_to_weight` calculation is
allowed; unrelated calculation kinds are ignored.

## Output and boundaries

The check ID is `thrust_to_weight`. Evidence includes thrust, mass, gravity,
computed weight, computed ratio, reported ratio and reconciliation status.

V7.7 does not persist the validation result, change task state, or move the
artifact from DRAFT. It also does not make a propulsion suitability policy
judgment such as imposing a minimum acceptable T/W ratio; such reusable policy
constraints belong to V7.9.
