# Gate — Phase 3 S3.7 Assumptions

S3.7 is GREEN only when:

- S3.6 is GREEN.
- installer preflight/apply pass.
- full target regression has zero failures.
- assumption provenance is exactly `ENGINEERING_ASSUMPTION`.
- facts and assumptions remain type/provenance-distinct.
- strict finite JSON values and bounded set size are enforced.
- duplicate assumption keys fail closed.
- snapshot hash/tamper validation passes.
- restart/open reconstructs the same AssumptionSet.
- operation-local rollback restores the previous snapshot on partial write.
- S3.1 SQLite tables remain unchanged.
- no assumption-to-fact promotion, decisions, general transaction API, cloud
  provider, or UAV execution-plane change is introduced.
- acceptance returns:
  `ASSUMPTIONS=PASS ... provenance=ENGINEERING_ASSUMPTION restart=PASS hash=PASS schema_tables=3`.

S3.8 decisions remains the sole next step.
