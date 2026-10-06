# Gate — Phase 3 S3.9 Project State Transaction Wrapper

S3.9 is GREEN only when:

- S3.8 is GREEN.
- installer preflight/apply pass.
- full target regression has zero failures.
- facts, assumptions, and decisions can be committed in one bounded wrapper.
- staged values must belong to the transaction project.
- duplicate category staging fails closed.
- visible write failure restores every prior target in the transaction.
- rollback/context-manager exit performs no persistent mutation.
- COMMITTED/ROLLED_BACK transactions cannot be reused.
- successful committed state survives restart.
- immutable S3.5 requirement history and REQ sequence are not rewritten.
- S3.1 SQLite tables remain unchanged.
- no arbitrary file mutation, raw SQL, retry loop, cloud provider, or UAV
  execution-plane behavior is introduced.
- acceptance returns:
  `PROJECT_TRANSACTION=PASS categories=3 files=6 restart=PASS rollback=PASS schema_tables=3 crash_recovery=reserved_for_S3_10`.

S3.10 restart/recovery tests remain the sole next Phase-3 step.
