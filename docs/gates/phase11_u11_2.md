# Gate U11.2 — Project → Mission Mapping

Parent: U11.1 GREEN / Phase 10 GREEN / CP8 GREEN / CP9 PENDING.

Acceptance requires deterministic read-only mapping from current approved
Project State to a U11.1 `SimulationProposal`: latest requirements APPROVED,
source artifacts APPROVED+current, explicit `simulation.mission_request`,
`MISSION-001`, `LOCAL_NED`, and correct positive-altitude to negative-Z waypoint
conversion.

U11.2 must not perform bridge/runtime-region validation, capability/mission
allowlisting, runtime invocation, UAV-run persistence, provider calls,
PX4/MAVSDK access, or simulation verdicting.

Target evidence:

```text
./uavctl test
python3 tools/check_phase11_project_mission_mapping.py
```

Expected acceptance begins with `PROJECT_MISSION_MAPPING=PASS` and ends with
`phase11=pending cp9=pending`.
