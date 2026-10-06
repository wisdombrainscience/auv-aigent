# Remote UAV Capability Platform — Current Status

Updated: 2026-09-29.

Scope: **PX4 SITL / Gazebo simulation only**. No real-UAV runtime is supported.

| Area | Status |
|---|---|
| NAV-001 hardened implementation | PASS by existing v1.1 SITL evidence; source preserved |
| Independent NAV-001 verifier | IMPLEMENTED; now streaming/bounded-memory |
| Execution telemetry boundary | FIXED; recorder stops before verification/post-flight actions |
| Evidence integrity | IMPLEMENTED; SHA256/read-only finalization |
| Evidence completeness guard | IMPLEMENTED for interactive executed attempts |
| Canonical test region | FIXED; UI/runtime policy resolves `config/experiment.yaml` |
| Continuous interactive session | IMPLEMENTED; successful run no longer auto-lands |
| Next-flight start | IMPLEMENTED; uses prior measured/verified navigation final |
| Explicit simulation Land | IMPLEMENTED as session lifecycle action |
| User-friendly map/target UI | IMPLEMENTED |
| Live trajectory | IMPLEMENTED with bounded telemetry buffer |
| Resource/disk guard | IMPLEMENTED |
| Historical metadata index | IMPLEMENTED with SQLite pagination |
| Console network boundary | ENFORCED localhost-only |
| Automated tests | 192 PASS in the GitHub-ready V7 deployment baseline; GitHub packaging adds its own CI/preflight checks |
| Generic capability registry/executor | IMPLEMENTED; NAV-001 + TAKEOFF-001 + LAND-001 + RETURN-HOME-001 registered |
| TAKEOFF-001 spec/module/verifier | PASS; 10/10 repeatability observed on target host with no callback-queue warnings after V5.1.1 |
| LAND-001 spec/module/verifier | PASS; 10/10 Takeoff→Land repeatability observed on target host with MAVSDK cleanup after every cycle |
| RETURN-HOME-001 spec/module/verifier | PASS on target host in Takeoff→NAV→ReturnHome→Land chain |
| Mission Executor / Mission Verifier | PASS; 10/10 target-host reference missions with child integrity/continuity/terminal checks PASS |
| Mission Builder UI | IMPLEMENTED in V6.1; multi-waypoint localhost plan builder backed by MISSION-001 runtime |
| Real UAV hardware | OUT OF SCOPE |

## Important correction from older STEP37A behavior

Earlier STEP37A verified NAV-001 while telemetry recording continued through landing. That could make finalized `telemetry.csv` differ from the dataset used to issue the verdict. The interactive runtime now stops and flushes capability telemetry before the verifier runs. Landing is outside that evidence window.

## Resource behavior

The runtime uses bounded in-memory structures and a storage pre-flight guard. Raw evidence is not silently deleted; new runs are blocked when configured quota/free-space limits are violated.

## V5.3.1

- Generic `capability-uav NAV-001` now satisfies the frozen NAV-001 evidence contract.
- Legacy validation/boundary/unit-gate artifacts are compatibility evidence derived from the generic lifecycle.
- RETURN-HOME acceptance should be rerun from a clean simulator chain after this fix.


## V6.0 Mission Executor / Mission Verifier

- Added frozen `MISSION-001` mission contract with TAKEOFF first and LAND last.
- Reference plan: TAKEOFF -> NAV -> RETURN-HOME -> LAND.
- One PX4/MAVSDK adapter is reused across all child capability windows.
- Every child retains its own finalized evidence and deterministic verifier.
- Mission-level verdict authority is `MissionVerifier`, not the mission executor.
- Mission verification checks sequence, child verdicts, child hashes, measured
  inter-step continuity, terminal landed state, and terminal Home distance.


## V6.1 Mission Builder

- Added `/mission` multi-waypoint planner without moving verdict authority into the browser.
- Mission Builder validates Home/waypoints against the canonical simulation region.
- Mission execution runs asynchronously through `MissionExperimentRunner` and exposes per-step progress.
- The mission owns its simulator lifecycle: fresh ground reset before Takeoff and simulator stop only after mission evidence/verdict finalization.
- Single-flight and mission starts are mutually blocked while the other execution mode is active.

## V6.2 Live Operations + Gazebo Desktop

- Mission Builder now exposes live current-step state, progress, measured UAV
  position/state, and a bounded actual-flight trajectory during mission execution.
- Single Flight start position is server-owned from the active session; stale
  browser coordinates can no longer trigger the old start-position mismatch path.
- One shared SimulationSupervisor now owns both Single Flight and Mission Builder.
- Gazebo desktop viewing is supported through authenticated X11/XWayland forwarding
  in `UAV_GAZEBO_GUI=auto` mode, with a browser **Open Gazebo** control and
  headless fallback.
- Automated regression suite: **165 PASS** in the V6.2.2 completion build.


## V6.2.1

- Corrected the default simulator pairing: Gazebo uses the official
  `px4io/px4-sitl-gazebo` runtime instead of the SIH-only `px4io/px4-sitl` image.
- The Gazebo image tag matches the PX4 revision previously pinned for SIH.
- `start-uav` validates Docker access and pulls the simulator image before serving
  the web UI, avoiding a hidden first-run pull inside a web request.
- Gazebo automatically runs headless when authenticated X11 is unavailable;
  `UAV_GAZEBO_GUI=on` instead fails early with a specific desktop-display error.
- Simulator output is persisted to `runs/.runtime/simulator.log` and surfaced in
  startup exceptions for actionable diagnostics.

## V6.2.2 completion hotfix

- Fixed the partially-applied `SimulationConfig` migration that caused `backend`
  constructor errors on the target host.
- `start_new()` now uses the backend-aware Gazebo/SIH command builder, including
  model/world/spawn pose, authenticated X11 mounts, and headless fallback.
- `UAV_GAZEBO_GUI=on` fails explicitly when desktop forwarding is unavailable;
  `auto` remains non-fatal and runs Gazebo physics headlessly.
- Simulator output is captured in `runs/.runtime/simulator.log`; startup exceptions
  include the log tail.
- Shutdown closes simulator logs and removes the private runtime Xauthority file.
- Added `./health-uav` for Docker/image/display/config diagnostics.
- Regression suite: **165 PASS**.


## V6.2.3

- Fixed Single Flight from a realistic Gazebo ground spawn (`z = 0`).
- Single Flight auto-takeoff is setup-only; NAV-001 remains the verifier authority
  for the navigation execution window.
- Added explicit `AUTO_TAKEOFF_SETUP` runtime phase and UI status.
- Added ground-bootstrap start-gate evidence and failure classification.
- Regression suite: **170 PASS**.

## V6.2.4

- Single Flight ground bootstrap now inserts an explicit PX4 Hold/stabilization
  hand-off before NAV-001.
- NAV Offboard startup seeds the measured current position before sending the
  destination target.
- Web status includes `STABILIZING` between `AUTO TAKEOFF` and `NAV RUNNING`.

## V6.2.5

- Single Flight server automatically transitions a successful `COMPLETED` run
  to `CONTINUE` when a new target is submitted.
- Fresh measured telemetry is authoritative at the next START_GATE; ordinary
  Hold drift no longer turns the second leg into a hard start mismatch.
- Frozen `last_verified_final_position_m` is kept distinct from the fresher
  post-Hold `current_vehicle_position_m` used by the UI.
- A flight requested after `LANDED` starts from the measured ground position so
  the simulation-only auto-takeoff bootstrap can run again.
- A previous non-PASS run requires simulator reset instead of silently
  continuing from an uncertain vehicle state.
- Regression suite: 177 passed.

## V6.2.6 runtime hardening

- Version: `0.9.6`
- Unit/regression tests: `181 passed`
- NAV controller completion uses a settled inner envelope; verifier tolerance is unchanged.
- ReturnHome-to-Land handoff is velocity-stabilized in PX4 Hold.
- LAND-001 stabilizes before native PX4 land to reduce Gazebo horizontal drift.
- Mission Builder terminal FAIL progress text corrected.

## V7 Deployment Edition

- Version: `1.0.1`.
- Preserves all V6.2.6 Single Flight, Gazebo, generic capability and MISSION-001
  behavior.
- Added `uavctl` unified deployment/maintenance command while keeping all legacy
  entrypoints.
- Fresh installs support persistent `UAV_DATA_DIR` so evidence survives source
  upgrades; legacy project-local `runs/` remains fully supported.
- Runtime metadata cache reduced to 64 runs; live UI trajectory buffers reduced
  to 300 points while raw verifier telemetry stays at 10 Hz.
- Single Flight and Mission Builder now use incremental telemetry transport,
  compact mission status, gzip JSON and HTTP/1.1 keep-alive.
- Resource disk-tree scans are cached for UI status but invalidated before run
  admission; SQLite backfill skips already indexed attempts.
- Added explicit storage status/retention/pinning, safe data migration, and clean
  release packaging with file/zip SHA256 manifests.
- Regression suite: **190 PASS**.
