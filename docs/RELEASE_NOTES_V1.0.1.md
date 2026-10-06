# Remote UAV v1.0.1 — Test/Installer Isolation Hotfix

v1.0.1 keeps the complete v1.0.0 flight, mission, Gazebo, deterministic
verification, evidence, storage, and web functionality unchanged.

## Fixed

- `./uavctl test` no longer inherits `UAV_DATA_DIR` or other runtime/simulator
  settings from `uav.env`. Tests always use their own temporary directories.
- The test suite includes an autouse environment-isolation fixture, so direct
  `pytest` runs are also protected from a user's active Remote UAV session.
- `./uavctl install` creates/updates the virtual environment with host
  `PYTHONPATH` removed and user-site packages disabled. This prevents ROS 2
  Python packages exposed by the shell from confusing pip's dependency check.
- Installation now runs `pip check` against the isolated virtual environment.

## Validation

- 194 unit/regression tests PASS.
- Tests PASS even when the parent shell exports `UAV_DATA_DIR` and simulator
  variables.
- Python compileall and shell syntax checks PASS.
