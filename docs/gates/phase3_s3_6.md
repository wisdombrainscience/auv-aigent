# Gate — Phase 3 S3.6 Facts

S3.6 is GREEN only when:

- S3.5 is GREEN.
- preflight/apply pass.
- full target regression has zero failures.
- facts accept only `USER_FACT` or `VERIFIED_FACT` provenance.
- `USER_FACT` is never silently promoted.
- fact keys and strict JSON values are bounded and deterministic.
- duplicate fact keys fail closed.
- `facts.json` and its SHA-256 sidecar survive restart and detect tampering.
- fact persistence does not change the S3.1 three-table SQLite schema.
- S3.6 adds no assumptions, decisions, general transaction API, AI/network code,
  cloud provider, or UAV execution-plane change.
- acceptance returns `FACTS=PASS ... restart=PASS hash=PASS schema_tables=3`.

S3.7 assumptions is the sole next roadmap step.
