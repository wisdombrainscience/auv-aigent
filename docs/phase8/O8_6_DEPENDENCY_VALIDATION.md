# O8.6 — Dependency Validation

O8.6 extends the cumulative Phase-8 `PlanValidator` with dependency semantics
before any proposed plan can become an approved/persisted DAG.

Checks are deterministic and ordered after O8.3-O8.5:

```text
plan.unique_task_ids
plan.known_roles
plan.known_output_schemas
plan.dependency_existence
plan.no_self_dependencies
plan.dag_acyclic
```

`plan.dependency_existence` requires every dependency TASK ID to name a task in
the same `TaskPlanProposal`. Evidence preserves proposal task/dependency order.

`plan.no_self_dependencies` rejects `TASK-X -> TASK-X` explicitly.

`plan.dag_acyclic` uses the same stable iterative DFS cycle semantics as T5.4.
The lexicographically smallest unvisited TASK ID is the deterministic traversal
start; dependency order remains the proposal declaration order. A cycle witness
repeats the starting TASK ID at the end.

Acyclicity fails closed when task identity/existence is not sound (duplicate task
IDs or missing dependencies). O8.6 does not guess a valid graph from ambiguous
input.

O8.6 does not enforce frozen planner resource policy. Task count, dependency
depth and retry/resource limits remain O8.7. O8.6 does not approve, persist,
schedule, or execute a DAG; persistence remains O8.8.
