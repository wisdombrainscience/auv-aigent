# V6.1 Mission Builder

The Mission Builder is the localhost user-facing authoring surface for `MISSION-001`.
It does not implement flight logic or verdict logic in JavaScript.

## Open

Start the normal console:

```bash
./start-uav
```

Then open:

- Single Flight: `http://127.0.0.1:8370/`
- Mission Builder: `http://127.0.0.1:8370/mission`

## Builder model

The user supplies:

- Home North/East;
- Takeoff altitude;
- one or more waypoints, each with North/East/positive Altitude;
- Return Home altitude;
- tolerances and per-step timeout.

The server converts the builder request into a frozen `MISSION-001` plan:

```text
TAKEOFF-001
  -> NAV-001 waypoint 1
  -> NAV-001 waypoint 2
  -> ...
  -> RETURN-HOME-001
  -> LAND-001
```

Altitude is positive-up in the browser and converted to LOCAL_NED negative-Z for
navigation targets.

## Runtime and authority

The Mission Builder API:

1. validates Home and every waypoint against the canonical simulation region;
2. applies the configured resource guard;
3. resets the simulator to Home on the ground;
4. executes the plan with one shared PX4/MAVSDK adapter;
5. keeps each child capability in its own frozen evidence window;
6. runs the independent child verifier for every step;
7. runs the independent `MissionVerifier` over child hashes, sequence, continuity,
   terminal Home distance, and landed state;
8. stops the simulator only after mission evidence and verdict finalization.

The browser displays these results but never creates a PASS/FAIL verdict itself.

## API

`POST /api/mission/start` accepts the Mission Builder request and returns a local
job id. `GET /api/mission/<job-id>/status` returns current step progress and the
final mission verification payload.

Mission execution is mutually exclusive with the interactive Single Flight run so
two browser actions cannot intentionally command the same simulator at once.
