# Gate V7.6 — Deterministic Reserve Calculation

Parent: V7.5 GREEN (`2022 passed` target evidence).
Frozen architecture: `RUAV-AI-ARCH-V8.0-BL1`.

## Scope

- Add deterministic `reserve` engineering validator.
- Require one reconciled V7.5 `battery_energy` calculation.
- Recompute reserve energy as `battery_wh * reserve_percent / 100`.
- Recompute usable energy as `battery_wh - reserve_wh`.
- Verify both reported values exactly using decimal bookkeeping.
- Return C1.7 `ValidationResult` only.

Reserved: V7.7 thrust-to-weight and every later Phase-7 gate.

## Required target validation

```bash
./uavctl test
python3 tools/check_phase7_reserve_calculation.py
```

Expected acceptance:

```text
RESERVE_CALCULATION=PASS artifact=ART-000001@v1 check=reserve status=PASS battery_wh=99.9 reserve_percent=20 reserve_wh=19.98 usable_wh=79.92 reserve_mismatch=FAIL usable_mismatch=FAIL restart=PASS artifact_state=DRAFT persisted_validation=none task_state=unchanged provider_calls=none thrust_to_weight=reserved_for_V7_7 lifecycle_transition=reserved cp7=pending
```

Do not mark V7.6 GREEN until both full target regression and this probe pass.
