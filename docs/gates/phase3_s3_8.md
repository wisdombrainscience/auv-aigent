# Gate — Phase 3 S3.8 Decisions

S3.8 is GREEN only when:

- S3.7 is GREEN.
- installer preflight/apply pass.
- full target regression has zero failures.
- Decision is a separate Project State category from Fact and Assumption.
- exact Decision fields are `key,value,rationale`.
- strict finite JSON values and bounded rationale/set size are enforced.
- duplicate decision keys fail closed.
- hash/tamper and symlink validation pass.
- restart/open reconstructs the same DecisionSet.
- partial paired write restores the previous DecisionSet.
- S3.1 SQLite tables remain unchanged.
- no new provenance class or decision authority is invented.
- no approval workflow, artifact lineage, general transaction API, cloud
  provider, or UAV execution-plane behavior is added.
- acceptance returns:
  `DECISIONS=PASS ... restart=PASS hash=PASS authority=external schema_tables=3 transactions=reserved_for_S3_9`.

S3.9 transaction wrapper remains the sole next step.
