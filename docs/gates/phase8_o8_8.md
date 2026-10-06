# Gate O8.8 — Persist Approved DAG

Parent: O8.7 GREEN / CP7 GREEN.

## Scope

- require cumulative PlanValidator PASS for all 10 checks;
- bind to the latest APPROVED requirement version;
- allocate canonical persisted TASK IDs and remap proposal dependencies;
- persist all TaskRecords + dependency edges atomically into the existing
  Phase-5 Task Registry;
- force persisted tasks to `CREATED`, `attempt=0`;
- provide bounded journal/receipt recovery for idempotent restart;
- keep Project State at 3 tables and Task Registry at 3 tables;
- do not schedule or execute tasks.

## Acceptance

```text
APPROVED_DAG_PERSISTENCE=PASS checks=10 proposed_tasks=3 persisted_tasks=3 id_remap=PASS dependencies=PASS statuses=CREATED idempotent=PASS restart=PASS invalid_plan_no_write=PASS requirement_binding=PASS registry_tables=3 project_schema_tables=3 task_execution=none provider_calls=none cp8=READY
```

CP8 may become GREEN only after target full regression and this acceptance probe
both pass.
