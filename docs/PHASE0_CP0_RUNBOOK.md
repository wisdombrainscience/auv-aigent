# Remote UAV V8 — PHASE 0 / CP0 Runbook

This runbook implements the first authorized action in frozen baseline `RUAV-AI-ARCH-V8.0-BL1`.

## Purpose

Freeze the **actual current target source tree** before any V8 AI implementation. CP0 is GREEN only when:

- the frozen architecture baseline is present and hashed;
- a deterministic source snapshot and manifest are created;
- the existing regression suite passes;
- one Single Flight/capability acceptance passes;
- one full Mission acceptance passes.

Do not use an older archive to claim CP0 for a newer target checkout.

## Install this overlay into the current Remote UAV project

Copy:

```text
docs/architecture/REMOTE_UAV_ARCHITECTURE_BASELINE_V8_0_FROZEN.md
tools/cp0_freeze.py
tools/cp0_verify.py
```

into the current target repository, preserving paths.

## First run: static freeze + regression only

From project root:

```bash
python3 tools/cp0_freeze.py --project .
```

This deliberately leaves acceptance checks `NOT_RUN`, so CP0 remains RED until the heavy simulation acceptance is explicitly requested.

## Full CP0 acceptance

If the project still uses the standard CLI and example files:

```bash
python3 tools/cp0_freeze.py \
  --project . \
  --run-acceptance
```

The tool auto-detects:

```text
Regression:     ./uavctl test       or ./test-uav
Single Flight:  ./capability-uav NAV-001 with config/examples/goto_away_5m_3m.json
Mission:        ./mission-uav with config/examples/mission_reference.json
```

If the current release changed these commands, provide explicit commands rather than changing the baseline silently:

```bash
python3 tools/cp0_freeze.py \
  --project . \
  --run-acceptance \
  --regression-cmd './uavctl test' \
  --single-flight-cmd '<CURRENT VALID SINGLE-FLIGHT ACCEPTANCE COMMAND>' \
  --mission-cmd '<CURRENT VALID MISSION ACCEPTANCE COMMAND>'
```

## Expected GREEN result

```text
CP0_STATUS=GREEN
SOURCE_MANIFEST_SHA256=<sha256>
SOURCE_ARCHIVE_SHA256=<sha256>
BASELINE_SHA256=<sha256>
```

The output directory contains:

```text
cp0_manifest.json
source_manifest.json
remote_uav_cp0_source_<timestamp>.zip
regression.log
single_flight.log
mission.log
```

Verify it:

```bash
python3 tools/cp0_verify.py reports/cp0/<timestamp>/cp0_manifest.json
```

Expected:

```text
CP0_VERIFY=PASS
```

## Gate rule

Do **not** begin PHASE 1 while CP0 is RED.

Typical RED reasons are intentionally explicit:

```text
regression_fail
single_flight_not_run
mission_not_run
baseline_file_missing
source_archive_not_created
```

Operational failures are not engineering failures. Fix the actual cause; do not retry indefinitely.

## CP0 Single Flight correction

For release 1.0.1, CP0 Single Flight must use `tools/cp0_single_flight_acceptance.py`. Raw `capability-uav NAV-001` from a ground spawn is intentionally rejected by NAV-001 preconditions and is not a valid Single Flight acceptance test.


## Mission startup readiness (Fix3)

Before `MISSION-001` begins after `--reset-simulation`, the Mission CLI now requires bounded read-only PX4 local-position readiness. A readiness timeout is an operational CP0 failure; the Mission is not retried automatically.
