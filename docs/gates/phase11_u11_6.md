# Phase 11 Gate U11.6 — Invoke Existing Runtime

Status: CANDIDATE until target apply, full regression, and acceptance probe pass.

## Scope

U11.6 adds the narrow execution handoff from the authorized UAV Bridge into the
existing Remote UAV mission runtime.

The invocation path is:

```text
SimulationProposal
  -> U11.3 bridge validation
  -> U11.4 capability authorization
  -> U11.5 mission authorization
  -> U11.6 invoke_existing_runtime()
  -> existing MissionExperimentRunner
  -> existing capability runtime
  -> existing independent child + mission verifiers
```

U11.6 does not create a replacement runtime and does not reinterpret the
runtime/verifier result. The existing `MissionExperimentResult` is returned
unchanged.

The bridge does not instantiate PX4, call raw MAVSDK, persist project linkage,
or grant any retry. UAV-run/project linkage remains reserved for U11.7.

CP9 remains pending.
