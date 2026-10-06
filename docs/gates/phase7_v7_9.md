# Gate V7.9 — Constraint Framework

Parent: V7.8 GREEN.

PASS requires a bounded deterministic numeric-constraint framework over supported V7.4–V7.8 source calculations. Thresholds are declared data, never hard-coded policy. Every constraint source validator must be requested and must PASS on the same artifact before comparison. Supported operators are `< <= == != >= >`; comparisons use finite Decimal values and canonical source units. Constraint IDs are stable/unique and constraint count is bounded.

V7.9 returns only C1.7 `ValidationResult`; artifact remains DRAFT, persisted validation remains null, task state is unchanged, provider calls are absent, and simulation-verifier authority is untouched.

Reserved: V7.10 validation issue schema and all review/lifecycle transitions.

Target acceptance:

```text
CONSTRAINT_FRAMEWORK=PASS artifact=ART-000001@v1 check=constraints status=PASS constraints=5 sources=5 policy_violation=FAIL source_invalid=FAIL restart=PASS artifact_state=DRAFT persisted_validation=none task_state=unchanged provider_calls=none validation_issue_schema=reserved_for_V7_10 lifecycle_transition=reserved cp7=pending
```
