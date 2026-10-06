# Gate U11.1 — Simulation Proposal Schema

Parent: X10.6 GREEN / Phase 10 GREEN / CP8 GREEN / CP9 PENDING.

Acceptance requires an immutable, deterministic, bounded simulation-proposal wire contract with exactly seven fields, canonical project/artifact lineage, strict finite JSON mission payload, and `SIMULATION_ONLY` execution scope.

U11.1 must not perform Project State mapping, bridge validation, capability/mission authorization, runtime invocation, UAV-run persistence, PX4/MAVSDK access, provider calls, or simulation verdicting.

Target evidence:

```text
./uavctl test
python3 tools/check_phase11_simulation_proposal_schema.py
```

Expected acceptance begins with `SIMULATION_PROPOSAL_SCHEMA=PASS` and ends with `phase11=pending cp9=pending`.
