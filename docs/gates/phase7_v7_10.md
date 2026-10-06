# Gate V7.10 — Validation Issue Schema

Parent: **V7.9 GREEN — Constraint Framework**  
Frozen architecture: `RUAV-AI-ARCH-V8.0-BL1`

## Gate requirements

V7.10 is GREEN only when all of the following hold on the target deployment:

1. the complete project regression passes;
2. failed C1.7 checks normalize deterministically into the V7.10 schema;
3. PASS-only validation produces no issues;
4. fingerprint identity is stable across restart and independent of evidence values;
5. issue evidence is preserved as immutable strict JSON;
6. no canonical `ISSUE-####` ID is allocated and no issue is persisted;
7. artifact remains `DRAFT`, persisted validation remains null, and task state is unchanged;
8. no provider/PX4/MAVSDK/Gazebo call is introduced.

Acceptance command:

```bash
python3 tools/check_phase7_validation_issue_schema.py
```

Expected marker:

```text
VALIDATION_ISSUE_SCHEMA=PASS
```

Passing V7.10 target evidence completes the frozen Phase-7 work sequence and
makes **CP7 — Engineering Validation Stable** eligible to be marked GREEN. CP7
must not be marked GREEN from package-build evidence alone.
