# V5 Generic Capability Framework

V5 begins the transition from a NAV-001-specific proof of concept to a reusable
UAV capability runtime.  The first V5 milestone is intentionally behavior
preserving: NAV-001 still uses the same implementation, verifier, evidence
contract, PX4 adapter and STEP37A user workflow that passed the V4.4 soak gate.

## New framework primitives

- `CapabilityDefinition`: declarative binding between capability ID, frozen
  specification, implementation module, source file and deterministic verifier.
- `CapabilityRegistry`: stores definitions and remains backward compatible with
  the original class-only registry API.
- `CapabilityExecutor`: generic runtime entry point that selects the module and
  verifier from the registry.  It does not own flight logic and does not decide
  PASS/FAIL.
- `CapabilityVerifier` protocol: minimum deterministic verifier interface.
- `src/capabilities/catalog.py`: the authoritative runtime catalog. NAV-001 is
  the first registered reference capability.

## Trust boundary

The refactor does not move verdict authority into the generic executor.

```text
CapabilityDefinition
        |
        v
CapabilityExecutor -----> capability module -----> UAVAdapter -----> PX4
        |
        +---------------> deterministic verifier -----> PASS / FAIL
```

The module may report completion. Only the registered independent verifier owns
technical acceptance.

## NAV-001 migration

STEP37A now resolves NAV-001 through `build_default_registry()` and
`CapabilityExecutor`.  The orchestration layer no longer constructs
`GoToTargetModule` or `GoToTargetVerifier` directly.

The frozen NAV-001 spec and source path remain unchanged.  This makes NAV-001 a
reference plugin instead of the framework's implicit hard-coded capability.

## Acceptance gate for this milestone

- all existing V4.4 tests continue to pass;
- new generic registry/executor/definition tests pass;
- NAV-001 spec-to-runtime binding validates against the frozen spec;
- STEP37A contains no direct construction of NAV-001 module or verifier;
- no changes to NAV-001 flight control or verification rules.

## Next milestone

After this patch is validated on the user's existing V4.4 environment, add
`TAKEOFF-001` as the first genuinely new capability.  It must have its own
frozen spec, deterministic verifier, tests and SITL evidence while using the
same generic registry/executor/evidence infrastructure.
