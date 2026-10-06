# MISSION-001 reference mission

`MISSION-001` is the first mission-level runtime built on the generic capability
framework. It executes independently verified child capabilities on one shared
PX4/MAVSDK adapter. The reference plan is:

`TAKEOFF-001 -> NAV-001 -> RETURN-HOME-001 -> LAND-001`

Each child keeps its own frozen telemetry/evidence window and deterministic
verdict. The mission verifier separately checks the declared/executed sequence,
child evidence integrity, authoritative child verdicts, measured inter-step
continuity, the terminal landed state, and the terminal horizontal distance to
Home. The mission executor itself is not a verdict authority.

Run in simulation:

```bash
./mission-uav \
  --mission-file config/examples/mission_reference.json \
  --scenario MISSION-V60 \
  --reset-simulation \
  --spawn-north 0 \
  --spawn-east 0 \
  --spawn-altitude 0 \
  --stop-simulation-after
```

The mission runner reuses one adapter across all child capability windows. This
keeps the PX4/MAVSDK vehicle session continuous while each capability freezes
and finalizes its own evidence independently.
