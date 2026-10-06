# STEP 37A — Local Web Experiment Console

## Purpose

STEP 37A upgrades the STEP 35 localhost UI from **input staging** to a **verified simulation experiment console** for the frozen NAV-001 v1.1 capability.

Runtime flow:

```text
Browser
  -> input validation
  -> runtime attempt/evidence
  -> PX4 SITL telemetry
  -> NAV-001 GoToTargetModule
  -> telemetry.csv
  -> independent NAV-001 verifier
  -> PASS / FAIL
```

## Important semantics

- Scope is **PX4 SITL simulation only**.
- The web console never accepts telemetry truth from the user.
- `start_position_m` is a requested simulation-start condition, not a spoofed vehicle state.
- The actual start is read from PX4 telemetry and checked against `start_position_tolerance_m`.
- The frozen NAV-001 `input.json` remains the canonical capability input; the new start-position field is kept in `scenario.json`.
- PASS/FAIL is returned only by `GoToTargetVerifier`.
- AI is not used to control the runtime or modify the verdict.
- The console binds to `127.0.0.1` by default.

## Run

The console expects the project's normal PX4 SITL/Gazebo simulation to already be running and publishing MAVSDK on UDP 14540.

```bash
cd "$HOME/UAV/st0_project"
PYTHONPATH="$PWD" "$PWD/.venv/bin/python" tools/step37a_console.py
```

Open:

```text
http://127.0.0.1:8370
```

For a code-only self-test:

```bash
PYTHONPATH="$PWD" "$PWD/.venv/bin/python" tools/step37a_console.py --self-test
```

## Definition of done for this step

The implementation is complete when a local PX4 SITL run produces:

- `raw_user_input.json`
- `input_validation.json`
- `input.json`
- `scenario.json`
- `measured_initial_state.json`
- `boundary_result.json`
- `unit_test_result.json`
- `telemetry.csv`
- `module_output.json`
- `verification.json`
- `events.jsonl`
- `metadata.json`
- `finalized.json`

A real-SITL PASS must still be demonstrated on the user's machine; this source change does not claim that verification was executed in this development environment.
