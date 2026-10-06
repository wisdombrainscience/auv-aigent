# U11.1 — Simulation Proposal Schema

## Scope

U11.1 introduces only the immutable data contract that crosses from approved engineering state toward the future UAV Bridge. It does not map Project State, validate bridge policy, authorize missions/capabilities, invoke Remote UAV, persist a UAV run, or decide simulation PASS/FAIL.

Contract version: `1.0`.

Exact wire fields:

```text
project_id
requirement_version
source_artifacts
scenario_id
execution_scope
mission_id
mission_payload
```

`execution_scope` is frozen to `SIMULATION_ONLY`. `project_id` is a canonical `UAV-PROJ-######` ID. `source_artifacts` contains one or more exact `ART-######@version` references. Approval/currentness of those artifacts is intentionally deferred to later bridge/project-state validation rather than asserted by this structural schema.

`mission_payload` is a bounded strict-JSON object: finite numbers only, immutable after parsing, maximum depth/node/serialized-size limits, and deterministic JSON serialization. U11.1 does not interpret capability or mission semantics inside the payload.

## Deferred responsibilities

- U11.2 — Project State → mission mapping
- U11.3 — bridge validation
- U11.4 — capability allowlist
- U11.5 — mission allowlist
- U11.6 — invoke existing runtime
- U11.7 — link UAV run back to project

A structurally valid future mission ID is therefore accepted at U11.1. This is not authorization to execute it.

The existing independent UAV verifier remains the sole simulation verdict authority.
