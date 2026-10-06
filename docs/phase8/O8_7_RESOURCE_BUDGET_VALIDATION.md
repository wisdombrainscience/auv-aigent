# O8.7 — Resource-budget Validation

O8.7 closes deterministic semantic approval for `TaskPlanProposal` before O8.8
may persist an approved DAG. It extends the cumulative `PlanValidator` with the
frozen V8 planner/runtime budgets and does not create or persist tasks itself.

The cumulative check order is now:

```text
plan.unique_task_ids
plan.known_roles
plan.known_output_schemas
plan.dependency_existence
plan.no_self_dependencies
plan.dag_acyclic
plan.task_count_budget
plan.dependency_depth_budget
plan.retry_budget
plan.runtime_resource_budget
```

## Frozen budgets

O8.7 enforces the frozen baseline exactly:

```text
planner.max_tasks_per_plan   = 30
planner.max_dependency_depth = 12
task.max_attempts            = 3
ai.max_parallel_agents       = 1
ai.max_loaded_models         = 1
task.timeout_s               = 300
```

The O8.1 parser remains defensively wider (`256` tasks and `1000` proposed
attempts). Those are parser safety bounds, not approval limits.

## Dependency depth

A task with no dependencies has depth `0`. A task depending directly on a root
has depth `1`. Therefore `max_dependency_depth=12` permits at most 12 dependency
edges on any path. Evidence includes one deterministic lexicographically
smallest witness among the deepest paths.

Depth calculation fails closed if duplicate task IDs, missing dependencies, or a
cycle prevent a trustworthy DAG from being established. O8.7 never guesses a
depth from an invalid graph.

## Runtime resource policy

`TaskPlanProposal` has no fields that can request extra model or execution
slots. The runtime resource check therefore verifies the existing deterministic
runtime constants directly: one parallel agent slot, one loaded model, and the
frozen 300-second task timeout. It does not load a model, schedule a task, or
invoke a provider.

## Non-goals

O8.7 does not approve or persist a DAG, allocate canonical IDs, mutate Project
State/TaskStore, schedule or execute tasks, call Ollama, or access
PX4/MAVSDK/Gazebo. Approved-DAG persistence remains O8.8.
