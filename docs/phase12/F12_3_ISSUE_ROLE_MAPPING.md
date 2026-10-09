# F12.3 — Simulation Issue → Engineering Role Mapping

## Purpose

Convert the deterministic issue taxonomy from F12.2 into a deterministic,
read-only owner route for the current active engineering team.

## Frozen MVP role boundary

The active Role Registry is still exactly:

```text
coordinator
requirements_engineer
uav_systems_engineer
reviewer
```

The specialized roles remain reserved. F12.3 does not silently activate
`simulation_engineer`, `verification_engineer`, `px4_flight_engineer`, or any
other reserved role.

## Mapping

All seven F12.2 issue types map to:

```text
uav_systems_engineer
```

This deliberately favors a safe current-MVP owner over inventing authority that
the frozen architecture has not activated.

## Preserved data

Routing preserves:

- exact issue fingerprint;
- `FailureDescriptor` and root-cause classification;
- project ID;
- requirement version;
- exact source artifact lineage;
- runtime run ID;
- mission/scenario IDs;
- verifier evidence hash;
- `VERIFIER_RESULT` provenance.

## Not in F12.3

- canonical ISSUE ID allocation or persistence;
- artifact lineage invalidation — F12.4;
- rework task generation — F12.5;
- bounded simulation retry — F12.6;
- `NEEDS_HUMAN` transition — F12.7;
- provider/model calls;
- PX4/MAVSDK access;
- verifier verdict reinterpretation.
