# TAKEOFF-001

`TAKEOFF-001` is the first additional capability implemented on the V5 generic
capability framework. It is simulation-only.

## Contract

The frozen specification is `specs/TAKEOFF-001.yaml`.

Inputs use:

- `frame = LOCAL_NED`;
- `target_altitude_m` as a **positive-up** operator-facing scalar;
- `altitude_tolerance_m`;
- `horizontal_tolerance_m`;
- `timeout_s`.

Measured position evidence remains LOCAL_NED, so the verifier converts the
final NED `z` coordinate to positive altitude with `altitude_m = -z`.

## Preconditions

The vehicle must be connected, not already airborne, and have valid local
position. Arming/takeoff readiness is handled through the PX4 adapter with the
bounded transient `COMMAND_DENIED` retry introduced in V5.0.1.

## Deterministic acceptance

The independent verifier requires all of the following:

- final altitude error is within `altitude_tolerance_m`;
- horizontal displacement from the first measured telemetry sample is within
  `horizontal_tolerance_m`;
- the final measured vehicle state is `AIRBORNE`;
- telemetry contains at least one non-airborne sample before the final airborne
  state, so an already-airborne vehicle cannot masquerade as a takeoff;
- telemetry timestamps are monotonic and localization stays valid;
- elapsed telemetry time is within `timeout_s`.

The module's own `status` is not an acceptance source.

## Generic experiment command

The generic runner is capability-ID driven; it is not a TAKEOFF-specific STEP
runner.

```bash
./capability-uav TAKEOFF-001 \
  --input-file config/examples/takeoff_3m.json \
  --scenario TAKEOFF-V51 \
  --reset-simulation \
  --spawn-north 0 \
  --spawn-east 0 \
  --spawn-altitude 0 \
  --post-action land \
  --stop-simulation-after
```

`--post-action land` occurs only after capability telemetry is frozen and the
deterministic verifier has issued its verdict. It therefore cannot alter the
evidence used for TAKEOFF-001 acceptance.

A successful first SITL gate should report `overall=PASS` and produce a
finalized evidence attempt under `runs/TAKEOFF-001/TAKEOFF-V51/`.
## Telemetry runtime

V5.1.1 keeps one continuously-drained MAVSDK subscription per telemetry topic and serves capability/recorder reads from the latest cached snapshot. This avoids repeated subscribe/read-one/close churn during takeoff while retaining capability-window-only CSV evidence and deterministic freeze-before-verify behavior.
