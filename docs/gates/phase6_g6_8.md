# Gate — Phase 6 G6.8 Result → DRAFT Artifact

Parent: **G6.7 GREEN — Context Manifest**.

## Gate contract

G6.8 is GREEN only when all of the following hold:

- one persisted `AI-RUN-*` plus verified Context Manifest can create one
  canonical Phase-4 artifact;
- the new artifact uses globally allocated `ART-######` identity;
- artifact type is exactly `ai.agent_result.v1`;
- artifact state is exactly `DRAFT` and validation result is null;
- task `input_artifacts` become the immutable artifact dependency versions;
- canonical content binds run, task, role, schema, Context Manifest hashes and
  the `PROPOSED` AgentResult;
- nested model content cannot promote lifecycle authority;
- restart/open verifies artifact, run, manifest, task and lineage binding;
- repeated creation of the same run recovers the same DRAFT artifact;
- malformed/tampered/drifted inputs fail closed;
- failed creation does not consume an `ART-*` sequence number;
- G6.6 Agent Run Registry remains exactly two tables;
- Phase-4 Artifact Registry remains exactly six tables;
- task state/attempt are unchanged;
- no provider call, review, validation, approval, integration or UAV command is
  introduced;
- focused tests, full regression and acceptance probe pass.

## Acceptance

```text
RESULT_DRAFT_ARTIFACT=PASS run=AI-RUN-000001 artifact=ART-000001@v1 status=DRAFT proposal=PASS lineage=PASS run_binding=PASS manifest_binding=PASS idempotent=PASS restart=PASS artifact_registry_tables=6 run_registry_tables=2 task_state=unchanged provider_calls=none review=reserved validation=reserved_for_phase7 approval=reserved cp6=READY
```

After successful target regression and this acceptance probe, G6.8 may be marked
GREEN and **CP6 — Single-agent execution stable** may be closed GREEN. The next
frozen phase is Phase 7 — Deterministic Engineering Trust.
