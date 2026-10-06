# Phase 11 Gate U11.4 — Capability Allowlist

Status: CANDIDATE until target apply, full regression, and acceptance probe pass.

## Scope

U11.4 adds explicit deny-by-default capability authorization after U11.3 bridge
validation.

Authorized capability IDs are exactly:

- `TAKEOFF-001`
- `NAV-001`
- `RETURN-HOME-001`
- `LAND-001`

The policy is intentionally independent from runtime registry membership. A
future capability is denied unless this reviewed allowlist is explicitly
changed.

U11.4 reuses the existing C1.7 `ValidationResult` contract and performs no
runtime execution or persistence.

Reserved gates remain:

- U11.5 mission allowlist;
- U11.6 runtime invocation;
- U11.7 UAV-run linkage.

CP9 remains pending.
