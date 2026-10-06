# V7.8 — Deterministic Mission Distance

V7.8 adds the frozen Phase-7 mission-distance calculator/validator only. It does not implement V7.9 constraints, V7.10 validation-issue schema, lifecycle transitions, mission execution, or simulation verdicts.

## Contract

Validator ID and calculation kind are both `mission_distance`.

The calculation contains exactly:

```json
{
  "kind": "mission_distance",
  "frame": "LOCAL_NED",
  "points_m": [[0,0,0], [0,0,-3], [3,4,-3]],
  "reported_distance_m": 8
}
```

`points_m` uses the project-frozen LOCAL_NED coordinate order `[north_m,east_m,down_m]`. Distance is the 3D polyline length, not the 2D Mission Builder map projection.

For each adjacent pair, the validator computes `sqrt(dn^2 + de^2 + dd^2)`, sums all segments under Decimal precision 50, then quantizes the final distance to `0.000001 m` with `ROUND_HALF_EVEN`. `reported_distance_m` must equal that deterministic result exactly.

The current Mission Builder supports at most 20 waypoints. V7.8 therefore bounds the engineering polyline to 24 points, enough for Home-ground, takeoff, 20 waypoints, return-home and landing. At least two points are required.

## Authority boundary

V7.8 returns only the existing C1.7 `ValidationResult`. It does not read telemetry, execute PX4/MAVSDK, or decide a mission PASS/FAIL. Independent UAV verification remains the sole simulation verdict authority. Artifact state stays DRAFT and validation evidence is not persisted at this gate.
