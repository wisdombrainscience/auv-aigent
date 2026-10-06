# Gate O8.6 — Dependency Validation

Parent: O8.5 GREEN / CP7 GREEN.

## Scope

- add `plan.dependency_existence`;
- add `plan.no_self_dependencies`;
- add `plan.dag_acyclic`;
- reuse T5.4 deterministic cycle witness semantics;
- keep O8.7 resource/depth/retry policy deferred;
- keep O8.8 approved-DAG persistence deferred.

## Acceptance

```text
DEPENDENCY_VALIDATION=PASS cumulative_checks=6 dependency_checks=3 existence=PASS missing=FAIL self_dependency=FAIL cycle=FAIL cycle_witness=PASS restart=PASS resource_budget=reserved_for_O8_7 approved_dag=reserved_for_O8_8 persistence=none task_execution=none provider_calls=none cp8=pending
```
