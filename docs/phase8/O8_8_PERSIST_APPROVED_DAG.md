# O8.8 — Persist Approved DAG

O8.8 closes Phase 8 by materializing a `TaskPlanProposal` only after the
cumulative O8.3–O8.7 `PlanValidator` returns PASS for all ten frozen checks.
The Orchestrator still never creates executable task state directly.

```text
TaskPlanProposal
      ↓
PlanValidator (10/10 PASS)
      ↓
latest APPROVED RequirementSet binding
      ↓
O8.8 atomic materialization
      ↓
existing Phase-5 Task Registry
      ↓
TaskRecord(status=CREATED, attempt=0)
```

## ID materialization

O8.1 TASK IDs are proposal-local candidate identities. O8.8 allocates real
global canonical TASK IDs from Project State in one transaction and remaps all
in-plan dependency references through the resulting one-to-one binding.

O8.8 does not assume candidate IDs are available persistent IDs.

## Atomicity

All TaskRecord rows, dependency edges, and the global TASK sequence update are
committed in one SQLite transaction using the existing Project State database
with the existing Task Registry attached. The Task Registry remains exactly
three tables and Project State remains exactly three tables.

A bounded prepared journal under `tasks/approved_dags/` supports interrupted
commit finalization. The immutable receipt is keyed by SHA-256 of canonical
`TaskPlanProposal` JSON. Retrying the same approved proposal in the same process
or after restart reopens the same receipt and does not allocate new TASK IDs.

## Requirement binding

Persistence requires `proposal.requirement_version` to be the latest Project
State requirement version and that snapshot must be `APPROVED`. A stale or
DRAFT requirement snapshot is rejected before TaskStore mutation.

## Non-goals

O8.8 does not move tasks beyond `CREATED`, resolve readiness, enqueue, assign,
run an agent, call a provider, or touch PX4/MAVSDK/Gazebo. Those authorities
remain in the already-frozen downstream runtime boundaries.
