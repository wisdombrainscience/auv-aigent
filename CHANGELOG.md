# Changelog

## [1.0.1] - 2026-09-30

- Isolate unit tests from deployed `uav.env` runtime state such as `UAV_DATA_DIR`.
- Isolate virtualenv/pip operations from ROS `PYTHONPATH` and user site packages.
- Run `pip check` after installation to verify the project environment independently.
- No flight, mission, verifier, evidence, or Gazebo behavior changes.

## [1.0.0] - 2026-09-30

### Added

- Deployment-oriented `uavctl` command surface.
- External runtime data root (`UAV_DATA_DIR`).
- Storage retention/pinning/cleanup tools.
- Bounded live telemetry and run metadata memory.
- Incremental/gzip web telemetry transport.
- Clean release packaging with SHA256 manifest/checksum.
- GitHub-ready documentation, CI workflow and contribution/security templates.

### Preserved

- Single Flight continuous navigation.
- Gazebo desktop/headless simulation.
- TAKEOFF-001, NAV-001, RETURN-HOME-001, LAND-001 deterministic verification.
- Mission Builder / MISSION-001 independent mission verification.
