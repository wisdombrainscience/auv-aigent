# U11.3 — Bridge Validation

## Purpose

U11.3 is the deterministic validation boundary between a structured simulation
proposal and later UAV-Bridge authorization/execution.

The AI layer still never calls PX4 directly. The validator only inspects the
proposal plus existing Remote UAV runtime policy and returns the existing C1.7
`ValidationResult` contract.

## Validation flow

```text
SimulationProposal
    ↓
SIMULATION_ONLY check
    ↓
scenario_id safe path-segment check
    ↓
strict MISSION-001 bridge contract
    ↓
existing MissionPlan parser
    ↓
canonical input policy
    ↓
permitted simulation region
    ↓
ValidationResult (PASS / FAIL)
```

## Canonical policy source

Before any runtime handoff, U11.3 also requires `scenario_id` to be a single portable filesystem-safe segment (`A-Z`, `a-z`, `0-9`, `_`, `-`, `.`, with a non-dot first character). This prevents path traversal because the existing runtime uses the scenario ID in evidence paths.

U11.3 does not duplicate numeric region limits. It loads:

```text
config/input_policy.yaml
    ↓ test_area_source
config/experiment.yaml
```

Therefore the same canonical simulation region already used by Remote UAV
runtime-input validation remains authoritative.

## Trust separation

A U11.3 PASS means only that the proposal is structurally and deterministically
valid for the currently implemented bridge contract and simulation policy.

It does **not** mean:

- the capabilities are authorized for the AI bridge (U11.4);
- the mission is authorized for the AI bridge (U11.5);
- the mission has executed (U11.6);
- a UAV run has been linked to Project State (U11.7);
- the simulation passed.

The independent UAV verifier remains the sole simulation PASS/FAIL authority.

## Side effects

None. No Project State write, no runtime evidence creation, no provider/model
call, no shell/network access, and no PX4/MAVSDK access.
