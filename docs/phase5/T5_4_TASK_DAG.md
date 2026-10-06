# T5.4 — Deterministic Task DAG Validation

T5.4 is the sole authority for cycle detection over persisted task dependency
declarations.

T5.3 answers whether dependency TASK IDs exist in the same project. T5.4 adds
the deterministic question: do those edges form a DAG?

`TaskDAGValidator` provides `detect_cycle(project)` and
`validate_project(project)`. A valid result contains `node_count`,
`edge_count`, and a deterministic `topological_order`.

Among graph nodes that are simultaneously eligible in the topological sort,
the canonical `TASK-xxxxxx` ID is the stable tie-breaker.

Cycle search is iterative rather than recursive. Self-cycles and longer cycles
return a stable witness with the starting task repeated at the end.

T5.4 invokes the T5.3 existence gate before classifying a non-empty registry.
A missing or cross-project dependency therefore fails closed and is never
reported as a DAG.

Defensive registry scan bounds are:

```text
MAX_TASK_DAG_NODES = 4096
MAX_TASK_DAG_EDGES = 122880
```

These do not replace the frozen planner policy:

```text
max_tasks_per_plan = 30
max_dependency_depth = 12
```

Those remain PlanValidator/planner concerns.

T5.4 is read-only. It does not mutate CREATED/BLOCKED/READY, decide dependency
completion, enqueue, schedule, increment attempts, apply timeouts, recover
INTERRUPTED work, cancel tasks, or execute agents/UAV actions.

T5.5 remains the sole owner of READY/BLOCKED resolution.
