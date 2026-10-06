# U11.2 — Project → Mission Mapping

## Scope

U11.2 deterministically maps current approved Project State into the immutable
`SimulationProposal` contract frozen by U11.1. It is a read-only adapter step;
it does not execute the UAV runtime and it does not own a simulation verdict.

Contract version: `1.0`.

The mapping requires:

- the latest RequirementSet version to be `APPROVED`;
- exactly one Project State fact named `simulation.mission_request`;
- one or more exact source artifact versions, each `APPROVED` and the current
  approved version of its logical artifact;
- simulation-only execution.

The `simulation.mission_request` fact must explicitly contain all mission input
fields; U11.2 does not inject UI defaults. It maps those positive-up operator
values into the existing `MISSION-001` plan and converts waypoint altitude to
LOCAL_NED negative-Z.

The generated mission sequence is deterministic:

```text
TAKEOFF-001
  → NAV-001 waypoint(s)
  → RETURN-HOME-001
  → LAND-001
```

## Deliberately deferred

U11.2 does **not** validate simulation-region bounds or runtime policy. It does
not authorize capabilities or missions and does not invoke PX4/MAVSDK. These
remain separate gates:

- U11.3 — bridge validation
- U11.4 — capability allowlist
- U11.5 — mission allowlist
- U11.6 — invoke existing runtime
- U11.7 — link UAV run back to project

The existing independent verifier remains the sole simulation PASS/FAIL
authority.
