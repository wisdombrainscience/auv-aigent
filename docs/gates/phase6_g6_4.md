# Gate — Phase 6 G6.4 Minimal Context Builder

G6.4 is GREEN only when:

- G6.3 is GREEN.
- context is structured rather than a free-form prompt.
- role policy and TaskRecord contract remain separate trusted sections.
- only explicitly referenced requirement/artifact versions are selected.
- referenced requirements must come from an APPROVED RequirementSet version.
- referenced parent artifacts must be APPROVED and content-hash verified.
- minimal parent artifact content is bounded UTF-8 text.
- project facts are included only through explicit bounded fact keys.
- missing/unapproved/non-text/oversized inputs fail closed without truncation.
- policy hierarchy is explicit and retrieved document content is never elevated
  to role/system policy.
- previous review issues, deterministic validation failures and retrieved
  documents remain reserved/empty rather than invented.
- task status/attempt/dependencies are not mutated.
- no provider/model call, prompt rendering, Agent run persistence, artifact
  creation, scheduler assignment, or UAV action is introduced.
- full target regression has zero failures.
- acceptance returns:
  `MINIMAL_CONTEXT=PASS task=TASK-000002 role=uav_systems_engineer requirements=1 artifacts=1 facts=1 approved_only=PASS policy_hierarchy=PASS restart=PASS provider_calls=none review_issues=reserved validation_failures=reserved documents=reserved output=reserved_for_G6_5 manifest=reserved_for_G6_7`.

G6.5 schema-enforced output remains the sole next step.
