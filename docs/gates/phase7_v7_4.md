# Gate V7.4 — Deterministic Mass Budget

Parent: V7.3 GREEN (`1971 passed` target evidence).
Frozen architecture: `RUAV-AI-ARCH-V8.0-BL1`.

## Scope

- Add deterministic `mass_budget` engineering validator.
- Recompute component mass sum in canonical kg.
- Verify declared total reconciliation.
- Verify total does not exceed declared mass limit.
- Return C1.7 `ValidationResult` only.

Reserved: V7.5 battery energy and every later Phase-7 gate.

## Required target validation

```bash
./uavctl test
python3 tools/check_phase7_mass_budget.py
```

Expected acceptance:

```text
MASS_BUDGET=PASS artifact=ART-000001@v1 check=mass_budget status=PASS components=3 computed_kg=3.5 limit_kg=4 over_limit=FAIL mismatch=FAIL restart=PASS artifact_state=DRAFT persisted_validation=none task_state=unchanged provider_calls=none battery_energy=reserved_for_V7_5 lifecycle_transition=reserved cp7=pending
```

Do not mark V7.4 GREEN until both full target regression and this probe pass.
