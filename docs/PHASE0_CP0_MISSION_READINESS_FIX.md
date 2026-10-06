# Phase 0 CP0 Mission Readiness Fix (Fix3)

## Observed failure

After a successful CP0 Single Flight and clean teardown, the automated Mission
started a fresh PX4/Gazebo container and immediately entered TAKEOFF-001.
The child evidence showed:

- `PRECONDITION_FAILED`
- `reason = localization_valid=false`
- vehicle still `DISARMED`
- capability execution time approximately zero
- evidence contract complete

The Mission had previously passed repeatedly when run manually.

## Root cause

`SimulationSupervisor.start_new()` waits for the Docker simulator container to
remain running for two seconds.  It does not wait for PX4 local-position health.

`tools/run_mission_experiment.py` previously printed `SIMULATION_READY`
immediately after `start_new()` and then opened the Mission.

PX4 adapter `connect()` waits for MAVSDK system connection, but connection alone
does not imply `health.is_local_position_ok`.

## Fix

Fix3 adds a bounded, read-only localization readiness gate in the Mission CLI:

1. start the simulator container;
2. connect a temporary PX4 adapter;
3. poll measured state until `connected=true` and `localization_valid=true`;
4. require the fresh vehicle to remain disarmed and on the ground;
5. close the readiness adapter;
6. execute Mission exactly once through the existing MissionExperimentRunner;
7. retain the independent capability and mission verifiers as verdict authority.

Timeout: 15 seconds. Poll period: 0.2 seconds.

The readiness gate never arms, takes off, enters Offboard, changes a target,
or creates a capability verdict. It therefore does not replace TAKEOFF-001.

## Architecture impact

None. This is a pre-CP0 startup sequencing correction in the existing
simulation CLI. It does not change the frozen AI architecture baseline,
capability boundaries, evidence contracts, or verifier authority.
