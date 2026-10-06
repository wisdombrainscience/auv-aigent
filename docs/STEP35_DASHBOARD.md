# NAV-001 STEP 35 Dashboard

This is a localhost-only, dependency-free dashboard for the accepted NAV-001
PX4 SITL simulation PoC.

It intentionally does **not**:
- start PX4/Gazebo,
- call MAVSDK,
- arm/takeoff/control any vehicle,
- modify verifier results,
- modify the accepted candidate.

It displays accepted/matrix status and stages user runtime-input artifacts:
`raw_user_input.json`, `input_validation.json`, and `input.json`.

The existing experiment runner / canonical validator remains authoritative.

## Install

Copy this bundle into `~/UAV/st0_project`.

## Self-test

```bash
export PROJECT="$HOME/UAV/st0_project"
cd "$PROJECT"
"$PROJECT/.venv/bin/python" tools/step35_dashboard.py --self-test
```

## Run

```bash
"$PROJECT/.venv/bin/python" tools/step35_dashboard.py
```

Open `http://127.0.0.1:8765`.

A successfully staged request is written under:
`reports/pipeline_v2/ui_inputs/<timestamp>/`
