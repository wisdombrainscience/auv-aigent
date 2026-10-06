# U11.6 — Invoke Existing Runtime

## Purpose

Connect the validated and authorized Phase-11 UAV Bridge to the existing Remote
UAV `MissionExperimentRunner` without creating a parallel execution path.

## Contract

`RUNTIME_INVOCATION_CONTRACT_VERSION = "1.0"`

Public API:

```python
await invoke_existing_runtime(
    project_root,
    proposal,
    adapter=trusted_runtime_adapter,
    step_hook=None,
    step_event_hook=None,
)
```

The function:

1. requires a `SimulationProposal`;
2. requires a trusted runtime adapter object;
3. executes U11.5 mission authorization before constructing the runtime;
4. forwards `mission_payload` unchanged;
5. forwards `scenario_id` unchanged;
6. invokes the existing `MissionExperimentRunner`;
7. returns its `MissionExperimentResult` unchanged.

If authorization fails, runtime construction is denied.

U11.6 does not own a verifier verdict, does not persist a project/UAV-run
relationship, does not instantiate a PX4 adapter, and does not access MAVSDK.
U11.7 remains responsible for linking the resulting UAV run back to Project
State.
