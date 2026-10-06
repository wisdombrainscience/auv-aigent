# Remote UAV v1.0.0 — Deployment Baseline

## Highlights

- PX4 SITL + Gazebo simulation backend with optional native desktop viewer.
- Single Flight with ground auto-takeoff, stabilized Offboard handoff, continuous multi-target operation and explicit Land.
- Mission Builder with multi-waypoint Takeoff → NAV → Return Home → Land flow.
- Generic capability registry/executor: TAKEOFF-001, NAV-001, RETURN-HOME-001, LAND-001.
- Independent deterministic capability and mission verification.
- Frozen telemetry/evidence boundary before verification and post-actions.
- Persistent metadata index, SHA256 evidence finalization and storage controls.
- Bounded in-memory telemetry/run state and incremental/gzip browser transport.
- Unified `uavctl` deployment/doctor/test/storage/package command surface.
- Runtime evidence externalized through `UAV_DATA_DIR` for safer upgrades.

## Validation baseline

- 192 regression tests PASS in the V7 deployment build.
- Prior target-host acceptance covered continuous Single Flight, Takeoff/Land repeatability, Return Home, multi-waypoint Mission and Gazebo GUI operation.

## Scope

Simulation only. Real UAV hardware operation is not supported by this release.
