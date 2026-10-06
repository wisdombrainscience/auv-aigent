# T5.3 — Task Dependencies

T5.3 materializes the `dependencies` field already frozen in the task contract.

## Registry schema v2

The per-project task registry evolves from two to three tables:

```text
tasks/task_registry.sqlite3
├── task_dependency_edges
├── task_registry_meta
└── tasks
```

Existing clean T5.1/T5.2 registries migrate from schema version 1 to version 2
by backfilling ordered dependency edges from immutable TaskRecord declarations.

The Phase-3 Project State database remains unchanged.

## Edge contract

Each declaration is stored as:

```text
child_task_id
ordinal
dependency_task_id
```

`ordinal` preserves the exact order in the task contract. Edge rows are written
transactionally with task creation and the registry verifier fails closed if
edge rows drift from the immutable TaskRecord.

## Dependency existence

T5.3 provides deterministic read/verification operations:

```text
dependencies(task)
direct_dependents(task)
missing_dependencies(project)
verify_project(project)
```

`verify_project()` passes only when every declared dependency TASK ID exists in
the same project registry. Cross-project references therefore fail the same
existence gate.

T5.3 deliberately keeps unresolved declarations persistable so a whole proposed
plan can be represented before validation. This matches the frozen boundary:
PlanValidator, not TaskStore creation, decides whether the proposed plan is
executable.

## Important separation from T5.4/T5.5

T5.3 does **not** detect cycles. A self-reference or longer cycle whose nodes all
exist can pass the T5.3 existence check; deterministic DAG cycle detection is
owned exclusively by T5.4.

T5.3 also does not inspect dependency completion or mutate CREATED/BLOCKED/READY.
READY resolution remains T5.5.

Still gated:

```text
T5.4 DAG cycle detection
T5.5 READY resolver
T5.6 priority queue
T5.7 attempt counter
T5.8 timeout
T5.9 interrupted recovery
T5.10 cancellation
T5.11 concurrency limit
```
