# Gate V7.2 — Finite-Number Checks

Status: candidate pending target validation.

## Parent

- CP6 GREEN.
- V7.1 GREEN — deterministic schema validation.
- Frozen baseline: `RUAV-AI-ARCH-V8.0-BL1`.

## Acceptance

V7.2 is GREEN only after the target deployment reports:

```text
FINITE_NUMBER_CHECK=PASS artifact=ART-000001@v1 check=finite_values status=PASS nonfinite_constant=FAIL overflow=FAIL huge_integer=FAIL nested=FAIL restart=PASS artifact_state=DRAFT persisted_validation=none task_state=unchanged provider_calls=none units=reserved_for_V7_3 lifecycle_transition=reserved cp7=pending
```

and the full target regression passes with zero failures.

## Frozen boundaries

- `finite_values` is the existing task validator identifier.
- Validation is deterministic and bounded.
- No validation evidence is persisted in V7.2.
- Artifact remains `DRAFT`.
- Task state/attempt are unchanged.
- V7.3 units and later engineering calculators remain reserved.
- No provider, PX4, MAVSDK, Gazebo, reviewer, approval, or independent-verifier authority is added.
