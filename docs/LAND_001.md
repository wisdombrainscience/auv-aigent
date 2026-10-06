# LAND-001

`LAND-001` is the independently verified landing capability on the V5 generic
capability framework. It is simulation-only.

## Contract

The frozen specification is `specs/LAND-001.yaml`.

Inputs use:

- `frame = LOCAL_NED`;
- `ground_altitude_tolerance_m` for the acceptable absolute altitude around the
  local zero reference after touchdown;
- `horizontal_tolerance_m` for allowed XY drift during descent;
- `timeout_s`.

Measured telemetry remains LOCAL_NED. Operator-facing altitude is derived as
`altitude_m = -z`.

## Preconditions

The vehicle must be connected, airborne, and have valid local position. An
already-landed vehicle is rejected instead of being counted as a successful
landing.

## Deterministic acceptance

The independent verifier requires all of the following:

- final absolute altitude is within `ground_altitude_tolerance_m` of local zero;
- horizontal displacement from the first measured telemetry sample is within
  `horizontal_tolerance_m`;
- the final measured vehicle state is not `AIRBORNE`;
- telemetry contains an `AIRBORNE` sample before the final non-airborne sample;
- telemetry timestamps are monotonic and localization stays valid;
- elapsed telemetry time is within `timeout_s`.

The module's own `status` is not an acceptance source.

## SITL acceptance sequence

Keep TAKEOFF and LAND as separate evidence windows. First take off and leave the
simulator running in Hold:

```bash
./capability-uav TAKEOFF-001 \
  --input-file config/examples/takeoff_3m.json \
  --scenario LAND-V52-SETUP \
  --reset-simulation \
  --spawn-north 0 \
  --spawn-east 0 \
  --spawn-altitude 0 \
  --post-action hold
```

Then execute LAND-001 against the same airborne PX4 session:

```bash
./capability-uav LAND-001 \
  --input-file config/examples/land_default.json \
  --scenario LAND-V52 \
  --stop-simulation-after
```

The second command's frozen telemetry and `LandVerifier` verdict are the
LAND-001 acceptance evidence. The simulator is stopped only after verification.
