# Phase 11 Gate U11.5 — Mission Allowlist

Status: CANDIDATE until target apply, full regression, and acceptance probe pass.

## Scope

U11.5 adds explicit deny-by-default mission authorization after U11.4
capability authorization.

Authorized mission IDs are exactly:

- `MISSION-001`

The policy is intentionally independent from runtime support. A future mission
is denied unless this reviewed allowlist is explicitly changed.

U11.5 reuses the existing C1.7 `ValidationResult` contract and performs no
runtime execution or persistence.

Reserved gates remain:

- U11.6 runtime invocation;
- U11.7 UAV-run linkage.

CP9 remains pending.
