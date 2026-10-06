# Gate — Phase 3 S3.5 Requirement Versions

S3.5 is GREEN only when:

- S3.4 is GREEN.
- preflight/apply pass.
- full target regression has zero failures.
- initial requirements receive persistent canonical REQ IDs.
- versions increase monotonically from 1 with no branching.
- supplied existing IDs must come from the immediately previous version.
- snapshots are immutable and hash-checked.
- old versions remain readable after later versions exist.
- restart/open reconstructs the same latest version.
- REQ sequence allocation rolls back on write failure.
- no RequirementSet is silently overwritten.
- the S3.1 SQLite table contract remains unchanged.
- no general transaction API, change-request impact analysis, artifact STALE
  propagation, cloud provider, or UAV execution-plane behavior is added.
- acceptance returns `REQUIREMENT_VERSIONS=PASS ... old_readable=PASS restart=PASS`.

S3.6 facts remains the sole next roadmap step.
