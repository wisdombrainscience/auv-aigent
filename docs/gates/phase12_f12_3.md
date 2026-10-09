# Gate F12.3 — Issue → Engineering Role Mapping

Status in this package: **CANDIDATE** until target installer, full regression, and acceptance evidence pass.

## Parent

- F12.2 GREEN — Simulation Issue Taxonomy
- F12.1 GREEN — Verifier Result Parser
- Phase 11 GREEN / CP9 GREEN
- U11.3-H1 Scenario Path Hardening VERIFIED

## Contract

F12.3 deterministically maps each already-classified `SimulationIssue` to an
**active engineering role**. It does not allocate `ISSUE-*`, persist a route,
invalidate artifacts, create rework tasks, authorize retry, call a model, or
invoke the UAV runtime.

The current frozen MVP Role Registry activates exactly four roles. The only
active engineering owner suitable for simulation-feedback rework is:

```text
uav_systems_engineer
```

Therefore every F12.2 issue type maps explicitly to `uav_systems_engineer`.
Reserved specialist roles such as `simulation_engineer` and
`verification_engineer` are **not activated** by this gate.

## Acceptance

```text
SIMULATION_ISSUE_ROLE_MAPPING=PASS
```

Phase 12 remains pending and CP10 remains pending.
