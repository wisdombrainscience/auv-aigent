# Phase 11 Gate U11.3 — Bridge Validation

Status: CANDIDATE until target apply, full regression, and acceptance probe pass.

## Scope

U11.3 adds deterministic validation at the UAV Bridge boundary. It validates:

- `SIMULATION_ONLY` execution scope;
- strict `MISSION-001` mission envelope and mapped step contracts;
- proposal mission ID consistency with the embedded mission payload;
- the existing deterministic `src.mission.model.MissionPlan` contract;
- canonical `config/input_policy.yaml` frame policy;
- canonical permitted simulation region resolved through `config/experiment.yaml`.

U11.3 is read-only. It does not authorize a capability or mission, invoke the
existing runtime, access PX4/MAVSDK, persist UAV evidence, or create a simulation
verdict.

Reserved gates remain:

- U11.4 capability allowlist;
- U11.5 mission allowlist;
- U11.6 runtime invocation;
- U11.7 UAV-run linkage.

CP9 remains pending.
