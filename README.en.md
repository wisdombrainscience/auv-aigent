# Remote UAV Mission Console

Simulation-only UAV capability and mission platform built around PX4 SITL, Gazebo, MAVSDK, deterministic verification, immutable evidence, and a localhost web console.

## Highlights

- Single Flight with ground auto-takeoff, continuous targets, Hold and Land.
- Multi-waypoint Mission Builder: Takeoff → NAV waypoints → Return Home → Land.
- Native Gazebo desktop viewing plus live browser trajectory.
- Generic capability registry/executor for TAKEOFF-001, NAV-001, RETURN-HOME-001 and LAND-001.
- Independent deterministic capability and mission verifiers.
- Bounded in-memory telemetry, incremental browser transport, SQLite metadata and explicit evidence retention.

## Quick start

```bash
./uavctl install --pull-sim
./uavctl doctor --gui
./uavctl start --gui
```

Open:

- `http://127.0.0.1:8370` — Single Flight
- `http://127.0.0.1:8370/mission` — Mission Builder

Current scope is **PX4 SITL/Gazebo simulation only**. Real UAV operation is not supported.

For the complete Vietnamese manual, see [docs/USER_MANUAL_VI.md](docs/USER_MANUAL_VI.md). For publishing this project on GitHub, see [docs/GITHUB_PUBLISHING.md](docs/GITHUB_PUBLISHING.md).
