# O8.3 — PlanValidator

O8.3 implements the deterministic approval boundary between the non-executable
O8.1 proposal and the later approved DAG:

```text
Orchestrator / coordinator
          ↓
TaskPlanProposal
          ↓
PlanValidator                 ← O8.3
          ↓
semantic checks O8.4–O8.7
          ↓
Approved Task DAG             ← O8.8
```

## Result contract

O8.3 reuses C1.7:

```text
ValidationResult
└── ValidationCheck[]
```

No new plan-result schema is introduced.

At this gate the default check set is exactly:

```text
plan.unique_task_ids
```

For a passing proposal the check evidence contains the task count and unique
count. For a failing proposal it additionally contains the duplicate candidate
`TASK-######` IDs in deterministic first-duplicate order.

## Layering

O8.1 intentionally allowed duplicate plan-level task IDs so O8.3 could reject
that semantic defect before tasks reach a scheduler or persistent TaskStore.

O8.3 deliberately does not inspect:

- RoleRegistry membership — O8.4;
- output-schema registry membership — O8.5;
- dependency existence, self-dependencies, cycles, or depth — O8.6/O8.7;
- `max_tasks_per_plan=30`, retry limits, execution-slot/resource policy — O8.7;
- approved-DAG persistence — O8.8.

Accordingly a proposal with unique IDs may still PASS O8.3 while containing a
well-formed unknown role/schema, a missing/cyclic dependency, or a retry count
above the frozen runtime policy. Those values remain available for their owning
later gates.

## Determinism and authority

`PlanValidator` is provider-free and persistence-free. It does not call Ollama,
create `TaskRecord` rows, allocate IDs, mutate Project State, schedule tasks, or
access PX4/MAVSDK/Gazebo.

The validator has no `approve_plan`, `persist_dag`, scheduler, or task-execution
method. O8.3 produces deterministic validation evidence only.
