# Gate V7.3 — Deterministic Unit Validation

## Parent

- CP6 GREEN.
- V7.1 GREEN — schema validation.
- V7.2 GREEN — finite-number checks.
- Frozen architecture `RUAV-AI-ARCH-V8.0-BL1` unchanged.

## Acceptance

Run:

```bash
python3 tools/check_phase7_unit_validation.py
```

Expected exact output:

```text
UNIT_VALIDATION=PASS artifact=ART-000001@v1 check=units status=PASS unknown_unit=FAIL suffix_mismatch=FAIL suffix_only=PASS restart=PASS artifact_state=DRAFT persisted_validation=none task_state=unchanged provider_calls=none mass_budget=reserved_for_V7_4 lifecycle_transition=reserved cp7=pending
```

## Gate rules

- `units` is the task validator identifier introduced by V7.3.
- Unit spellings are canonical and case-sensitive; V7.3 performs no conversion.
- Unit declarations/suffixes are checked, but domain-specific unit completeness remains
  owned by later calculators.
- Artifact content must remain Phase-4 hash verified.
- Result is C1.7 `ValidationResult` only.
- Artifact stays `DRAFT`; `validation_result` remains unpersisted.
- Task state/attempt is unchanged.
- No provider/model/UAV-runtime call is introduced.
- V7.4 mass budget and later calculators remain reserved.
