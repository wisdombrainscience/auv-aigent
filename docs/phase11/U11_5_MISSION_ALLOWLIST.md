# U11.5 — Mission Allowlist

U11.5 adds the mission-level authorization boundary to the Phase 11 UAV Bridge.

## Contract

- `MISSION_ALLOWLIST_CONTRACT_VERSION = 1.0`
- explicit static allowlist: `MISSION-001`
- deny by default for every other mission ID
- U11.4 capability authorization is required before the overall U11.5 result
  can pass
- authorization does not derive from runtime mission support

## Trust boundary

The gate is read-only and deterministic. It does not:

- invoke `MISSION-001`;
- invoke any capability;
- access PX4 or raw MAVSDK;
- persist a UAV run;
- create a verifier verdict;
- grant permission to future missions automatically.

Runtime invocation remains U11.6. UAV-run linkage remains U11.7. The
independent verifier remains the sole simulation verdict authority. CP9 remains
pending until the complete UAV Bridge is stable.
