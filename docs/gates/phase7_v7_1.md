# Gate V7.1 — Schema Validation

Status: candidate pending target validation.

## Frozen parent

- CP6 GREEN
- G6.8 GREEN
- architecture: `RUAV-AI-ARCH-V8.0-BL1`

## Acceptance

Required on target:

```text
./uavctl test
python3 tools/check_phase7_schema_validation.py
```

Acceptance probe must report:

```text
SCHEMA_VALIDATION=PASS artifact=ART-000001@v1 check=schema status=PASS invalid=FAIL unknown_type=FAIL restart=PASS artifact_state=DRAFT persisted_validation=none task_state=unchanged provider_calls=none finite_numbers=reserved_for_V7_2 units=reserved_for_V7_3 lifecycle_transition=reserved cp7=pending
```

## Gate boundary

V7.1 returns a C1.7 `ValidationResult` only. It does not persist validation,
transition lifecycle, review, approve, call a model, or touch the UAV runtime.
