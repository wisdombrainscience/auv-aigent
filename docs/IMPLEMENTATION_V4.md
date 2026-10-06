# Implementation V4 — Completed Changes

Date: 2026-09-29

This refactor implements the final NAV-001 interactive simulation foundation described in `docs/FINAL_PIPELINE.md`.

## Correctness / evidence

- Fixed STEP37A ordering so capability telemetry is stopped and flushed before the authoritative verifier runs.
- Removed automatic landing from successful NAV-001 runs; post-flight actions no longer mutate navigation evidence.
- Added `changed_files.json` marker for runtime experiments that do not mutate source.
- Added `EvidenceContractValidator` and contract result artifact before finalization.
- Added a regression test that deliberately moves the fake UAV after verification and proves the finalized telemetry still reproduces the original verdict.

## Runtime / resource behavior

- Rewrote NAV-001 verifier to stream CSV rows rather than materializing the entire file.
- Added bounded `ExperimentStore` cache.
- Added bounded live telemetry ring buffers.
- Added efficient telemetry tail fallback.
- Batched telemetry flushes while guaranteeing a final flush at execution end.
- Added resource policy and pre-flight guard for maximum timeout, runs quota, and minimum free disk.
- Added SQLite run summary/history index instead of retaining history in RAM.
- Added Docker log rotation limits for the simulator container.

## Session / user workflow

- Successful simulation sessions stay alive after GoToTarget.
- The next run automatically starts from the previous measured/verified navigation final position.
- Added explicit session `Land` action.
- Added `RESET_REQUIRED` handling for uncertain/fatal state.
- Added Home/initial position to session state.

## UI

- Replaced the developer-style STEP37A form with a user-facing Vietnamese mission console.
- Positive Altitude is shown in UI while LOCAL_NED Down remains internal.
- Per-flight start is displayed automatically and is not user-entered.
- Added click-on-map target selection.
- Added Home, Start, UAV, Target, planned-line reference, acceptance radius and actual trajectory.
- Added 3D telemetry trajectory view.
- Added live distance/error/verdict cards, result summary, history and resource status.
- Added explicit Start/Restart Simulation and Land semantics.
- Enforced localhost-only console binding.

## Configuration / deployment

- `config/experiment.yaml` is now the canonical simulation region source.
- `config/input_policy.yaml` resolves that source instead of duplicating region numbers.
- Added `config/resource_policy.yaml`.
- Added `setup-uav` and `start-uav` launch scripts.
- Made `project_healthcheck.py` project-relative and robust to a `.venv` without pytest.
- Added explicit setuptools package discovery and version `0.2.0`.
- Distribution should exclude `.venv`, caches, old patch bundles and generated build metadata.

## Validation performed in this environment

- 86/86 unit/regression tests PASS.
- STEP37A console self-test PASS.
- HTTP smoke test PASS for `/`, `/api/config`, `/api/resources`, `/api/history`.
- Localhost-only host restriction verified.
- Source wheel builds successfully with local build dependencies using `--no-build-isolation`.
- Full live PX4/Gazebo SITL flight was not executed in this environment because the runtime MAVSDK/Docker simulation stack is host-dependent; existing NAV-001 v1.1 SITL evidence remains preserved.

## Intentionally not claimed complete

The following remain separate milestones and should not be faked by reusing session lifecycle actions:

- independently frozen and verified `TakeoffModule`;
- independently frozen and verified `LandModule`;
- independently frozen and verified `ReturnHomeModule`;
- generic multi-capability Mission Executor + Mission Verifier;
- real-UAV hardware operation.
