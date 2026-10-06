# Gate — Phase 3 S3.4 RequirementSet

S3.4 is GREEN only when:

- S3.3 is GREEN.
- installer preflight/apply pass.
- full target regression has zero failures.
- Requirement requires canonical `REQ-*` identity.
- Requirement provenance is exactly `USER_REQUIREMENT`.
- RequirementSet requires canonical project identity.
- only `DRAFT` and `APPROVED` statuses are representable.
- duplicate requirement IDs fail closed.
- payload size/count limits are enforced without truncation.
- strict JSON round trip preserves requirement order and text.
- no `version`, `previous_version`, or change-request field is introduced.
- no requirement ID allocation or RequirementSet persistence is introduced.
- no facts, assumptions, decisions, artifact invalidation, agent logic, cloud
  provider, or UAV execution-plane change is introduced.
- acceptance returns:
  `REQUIREMENT_SET=PASS ... versioning=reserved_for_S3_5`.

S3.5 remains the sole next step after this gate.
