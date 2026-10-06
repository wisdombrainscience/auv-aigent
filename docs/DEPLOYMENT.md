# Deployment and Resource Guidance — V7 Deployment Edition

This release keeps the complete V6.2.6 flight/capability/mission feature set and
changes only deployment, persistence, memory bounds, browser traffic, and
storage maintenance.

## 1. Recommended end-user installation

Requirements:

- Linux desktop/server;
- Python 3.10+;
- Docker with permission to run `docker ps` without an interactive sudo prompt;
- `xauth` only if the native Gazebo desktop window is required.

From the extracted release directory:

```bash
./install-uav --pull-sim
./uavctl doctor --gui
./uavctl start --gui
```

`--pull-sim` is optional. Without it, the pinned PX4/Gazebo image is downloaded
on the first simulator start.

For headless deployment:

```bash
./install-uav
./uavctl doctor
./uavctl start --headless
```

For developers, `setup-uav` is preserved and installs the pytest extras:

```bash
./setup-uav
./test-uav
```

## 2. Unified command surface

```text
./uavctl install [--dev] [--pull-sim]
./uavctl start [--gui|--headless] [--gazebo|--sih] [--port N]
./uavctl doctor [--gui]
./uavctl test
./uavctl storage status
./uavctl storage cleanup [--apply]
./uavctl storage pin <attempt-dir>
./uavctl storage unpin <attempt-dir>
./uavctl migrate-data [old-project-or-runs] [--apply]
./uavctl package [output-dir]
./uavctl version
```

Legacy commands remain available. No capability or mission workflow was removed.

## 3. Persistent data across upgrades

A fresh `install-uav` creates `uav.env` with:

```bash
UAV_DATA_DIR="$HOME/.local/share/remote-uav"
```

Runtime data then lives under:

```text
~/.local/share/remote-uav/runs/
```

The source folder can therefore be replaced by a new release without deleting
flight history/evidence. `uav.env` is local configuration and is deliberately
excluded from release archives.

If `UAV_DATA_DIR` is not set, the historical behavior is preserved and evidence
is written to `<project>/runs`.

To migrate an older project safely:

```bash
./uavctl migrate-data ~/UAV/old-project          # dry-run
./uavctl migrate-data ~/UAV/old-project --apply
```

The migration never deletes the old copy and aborts if conflicting destination
files already exist.

## 4. Memory optimization

`config/resource_policy.yaml` now defaults to:

- 64 active/recent run records in process memory;
- 300 live trajectory points per active run;
- 120 new telemetry points maximum per browser response;
- raw evidence telemetry still recorded at 10 Hz.

Single Flight live buffers are bounded by both points-per-run and number of
remembered runs. Mission live trajectories are held in a separate bounded ring
buffer instead of copying the complete trajectory into the job dictionary five
times per second.

Historical metadata remains in SQLite; raw evidence remains on disk.

## 5. Browser/network optimization

The browser no longer downloads the complete trajectory on every status poll.
It sends a telemetry timestamp cursor and receives only newer samples.

Additional transport changes:

- JSON responses larger than 2 KiB use gzip when supported by the browser;
- HTTP/1.1 keep-alive avoids a new TCP connection for every local fetch;
- Single Flight status defaults to 750 ms polling;
- Mission status defaults to 500 ms polling;
- storage/simulator resource panels default to 15 s polling;
- Mission status uses a compact response and gets trajectory through a separate
  incremental endpoint.

These are UI/transport changes only. They do not alter deterministic evidence
or PASS/FAIL.

## 6. Disk I/O optimization

Resource status used to recursively scan the full `runs` tree on every UI
resource poll. The size result now has a short 5 second cache. Immediately
before any new execution, the cache is invalidated and an exact quota scan is
performed, so the safety guard remains authoritative.

The NAV metadata index also skips attempts already present in SQLite during
startup backfill, avoiding repeated JSON reads as history grows.

## 7. Evidence retention and cleanup

The runtime still **does not auto-delete evidence**. Cleanup is explicit.

Inspect storage:

```bash
./uavctl storage status
```

Preview cleanup candidates:

```bash
./uavctl storage cleanup
```

Apply only after reviewing the dry-run:

```bash
./uavctl storage cleanup --apply
```

Default retention policy:

- PASS attempts: 30 days;
- FAIL attempts: 90 days;
- incomplete attempts: 7 days;
- always retain the newest 20 attempts per task/scenario;
- pinned attempts are never selected.

Pin a reference attempt without modifying its immutable evidence directory:

```bash
./uavctl storage pin runs/NAV-001/SCENARIO/attempt_001
```

Pins are stored outside evidence in `runs/.retention_pins.json`.

## 8. Clean release packaging

Build a redistributable archive:

```bash
./uavctl package
```

The resulting archive excludes:

- `.venv`;
- `runs/` and `reports/`;
- Python/test caches;
- local `uav.env`;
- patch residue and local zip bundles.

It contains a `RELEASE_MANIFEST.json` with SHA256 for every source file and a
SHA256 file beside the final zip.

## 9. Runtime profiles

Desktop with Gazebo GUI:

```bash
./uavctl start --gui
```

Gazebo physics without desktop window:

```bash
./uavctl start --headless
```

Lightweight SIH compatibility backend:

```bash
./uavctl start --sih
```

The default web server remains localhost-only.

## 10. Upgrade acceptance gate

After replacing the source release:

```bash
./setup-uav
./test-uav
./uavctl doctor --gui
```

Then verify one continuous Single Flight chain and one Mission Builder mission.
The deterministic capability/mission verifiers remain the acceptance authority.
