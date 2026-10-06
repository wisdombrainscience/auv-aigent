# U11.4 — Capability Allowlist

## Purpose

U11.4 adds explicit capability authorization at the UAV Bridge boundary after
U11.3 deterministic validation.

The policy is **deny by default**. Runtime availability is not authorization.
Adding a new capability to the existing Remote UAV capability registry does not
automatically grant the AI Bridge permission to use it.

## Authorized capabilities

The U11.4 bridge allowlist is exactly:

```text
TAKEOFF-001
NAV-001
RETURN-HOME-001
LAND-001
```

The allowlist is a static reviewed bridge policy, not a reflection of the
runtime registry.

## Authorization flow

```text
SimulationProposal
    ↓
U11.3 Bridge Validation
    ↓ must PASS
Extract mission step capability IDs
    ↓
Explicit U11.4 allowlist
    ├── all allowed → PASS
    └── any unknown → FAIL / deny by default
```

The output reuses the existing C1.7 `ValidationResult` contract. A PASS means
only that U11.3 validation passed and every observed capability ID is authorized
for the AI Bridge.

## Trust separation

U11.4 does **not** authorize a mission ID. Mission authorization remains U11.5.
It does not invoke the runtime (U11.6), persist/link a UAV run (U11.7), access
PX4/MAVSDK, or own the simulation verdict.

The independent deterministic verifier remains the sole simulation PASS/FAIL
authority.

## Side effects

None. Authorization is read-only and deterministic.
