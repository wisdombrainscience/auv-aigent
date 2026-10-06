# Gate O8.7 — Resource-budget Validation

Parent: O8.6 GREEN / CP7 GREEN.

Acceptance requires:

- cumulative `PlanValidator` has exactly 10 ordered checks;
- `plan.task_count_budget` enforces `max_tasks_per_plan=30`;
- `plan.dependency_depth_budget` enforces `max_dependency_depth=12` and fails
  closed on an invalid graph;
- `plan.retry_budget` enforces `max_attempts=3`;
- `plan.runtime_resource_budget` verifies `max_parallel_agents=1`,
  `max_loaded_models=1`, and `task_timeout_s=300` from the existing runtime;
- restart output is deterministic;
- no persistence, task execution, provider call, or O8.8 behavior is added.

Expected acceptance marker:

```text
RESOURCE_BUDGET_VALIDATION=PASS cumulative_checks=10 resource_checks=4 max_tasks=30 task_count_over=FAIL max_dependency_depth=12 depth_over=FAIL max_attempts=3 retry_over=FAIL max_parallel_agents=1 max_loaded_models=1 task_timeout_s=300 runtime_budget=PASS restart=PASS approved_dag=reserved_for_O8_8 persistence=none task_execution=none provider_calls=none cp8=pending
```
