# RETURN-HOME-001

`RETURN-HOME-001` is the fourth registered capability in the generic runtime.
It returns an already-airborne simulated vehicle to a declared local home
horizontal anchor while remaining airborne at a requested return altitude.
Landing remains a separate `LAND-001` capability.

## Inputs

The reference example is `config/examples/return_home_3m.json`:

```json
{
  "task_id": "RETURN-HOME-001",
  "frame": "LOCAL_NED",
  "home_position_m": [0.0, 0.0],
  "return_altitude_m": 3.0,
  "horizontal_tolerance_m": 0.5,
  "altitude_tolerance_m": 0.5,
  "timeout_s": 30.0
}
```

`home_position_m` is `[north_m, east_m]`. `return_altitude_m` is positive-up;
the adapter receives a NED target with `z = -return_altitude_m`.

The vehicle must already be airborne, have valid localization, and begin
outside the home horizontal tolerance. This prevents a vehicle already at home
from trivially satisfying a return-home experiment.

## Acceptance

The deterministic verifier reads only frozen telemetry plus canonical input and
checks:

- final horizontal distance to home is within tolerance;
- final altitude is within the return-altitude tolerance;
- the final vehicle state remains `AIRBORNE`;
- telemetry proves a return transition from outside the home tolerance to
  inside it;
- elapsed time is within the requested timeout;
- localization remains valid and telemetry timestamps are monotonic.

`module_output.status` is not an acceptance source.

## SITL acceptance sequence

Start at ground, take off, move away from home, then return:

```bash
./capability-uav TAKEOFF-001 \
  --input-file config/examples/takeoff_3m.json \
  --scenario RTH-V53-SETUP-TAKEOFF \
  --reset-simulation \
  --spawn-north 0 --spawn-east 0 --spawn-altitude 0 \
  --post-action hold

./capability-uav NAV-001 \
  --input-file config/examples/goto_away_5m_3m.json \
  --scenario RTH-V53-SETUP-AWAY \
  --post-action hold

./capability-uav RETURN-HOME-001 \
  --input-file config/examples/return_home_3m.json \
  --scenario RTH-V53 \
  --post-action hold

./capability-uav LAND-001 \
  --input-file config/examples/land_default.json \
  --scenario RTH-V53-CLEANUP \
  --stop-simulation-after
```
