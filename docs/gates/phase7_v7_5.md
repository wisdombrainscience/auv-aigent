# Gate V7.5 — Deterministic Battery Energy

Parent: V7.4 GREEN (`1998 passed` target evidence).
Frozen architecture: `RUAV-AI-ARCH-V8.0-BL1`.

## Scope

- Add deterministic `battery_energy` engineering validator.
- Recompute nominal energy as canonical `Wh = V * Ah`.
- Verify reported energy reconciliation exactly using decimal bookkeeping.
- Return C1.7 `ValidationResult` only.

Reserved: V7.6 reserve calculation and every later Phase-7 gate.

## Required target validation

```bash
./uavctl test
python3 tools/check_phase7_battery_energy.py
```

Expected acceptance:

```text
BATTERY_ENERGY=PASS artifact=ART-000001@v1 check=battery_energy status=PASS voltage_v=22.2 capacity_ah=4.5 computed_wh=99.9 reported_wh=99.9 mismatch=FAIL restart=PASS artifact_state=DRAFT persisted_validation=none task_state=unchanged provider_calls=none reserve=reserved_for_V7_6 lifecycle_transition=reserved cp7=pending
```

Do not mark V7.5 GREEN until both full target regression and this probe pass.
