# G6.8 — Result → DRAFT Artifact

## Scope

G6.8 closes the Phase-6 single-agent data path by converting one persisted,
schema-enforced `AI-RUN-*` proposal with a verified G6.7 Context Manifest into
one Phase-4 artifact version.

The Phase-4 Artifact Registry remains authoritative for artifact identity,
content hash, dependency edges, lifecycle state and versioning.

## Data path

```text
TaskRecord
  ↓
MinimalAgentContext
  ↓
SchemaEnforcedAgentOutput
  ↓
AgentRunRecord (AI-RUN-######)
  ↓
ContextManifest
  ↓
ResultDraftArtifactStore
  ↓
ArtifactVersionStore.create_artifact(...)
  ↓
ART-###### @ v1, status=DRAFT
```

G6.8 does not create a parallel artifact registry and does not modify the
Phase-4 artifact schema.

## Artifact type and payload

G6.8 uses the frozen local artifact type:

```text
ai.agent_result.v1
```

The content-addressed artifact payload contains only the deterministic binding
needed to reproduce the proposal origin:

```text
contract_version
run_id
project_id
task_id
role_id
agent_identity
schema_id
context_manifest_sha256
context_sha256
result
```

The `result` is still an `AgentResult` with status exactly `PROPOSED`.
Model-authored nested fields are data only. They cannot promote the real
Artifact Registry lifecycle state.

## DRAFT-only authority boundary

Every new artifact is created through the existing `ArtifactVersionStore`.
Therefore the canonical Phase-4 rules apply:

- `ART-######` is globally allocated by Project State;
- version starts at `1`;
- lifecycle state starts at `DRAFT`;
- `validation_result` is `null`;
- exact content bytes are SHA-256 addressed;
- dependency edges are immutable;
- content and registry records are verified on reopen.

G6.8 exposes no review, deterministic validation, approval, integration,
provider execution or UAV-control API.

## Lineage

The output artifact declares exactly the task's persisted
`input_artifacts` tuple as its dependency versions. In this project contract the
tuple may contain canonical `REQ-*` or `ART-*` version references.

G6.7 Context Manifest hashes preserve the remaining context binding, including
selected facts that do not have canonical artifact/requirement IDs.

## Restart and duplicate protection

`ResultDraftArtifactStore.open_artifact()` reopens and verifies:

- Phase-4 artifact record/content integrity;
- persisted `AI-RUN-*`;
- G6.7 Context Manifest;
- task identity, role and output schema;
- dependency lineage;
- canonical payload bytes.

Repeated creation for the same immutable run/context/result recovers the same
DRAFT artifact by its deterministic content hash under a bounded per-project
search. A project-local file lock prevents concurrent duplicate allocation.
If the existing result artifact has already moved beyond `DRAFT`, G6.8 fails
closed instead of creating another artifact.

Operational recovery scan bound:

```text
MAX_RESULT_ARTIFACT_RECOVERY_CANDIDATES = 4096
```

This is an operational defensive bound, not a planner semantic limit.

## Non-goals

G6.8 does not implement:

- `DRAFT → REVIEWED`;
- deterministic engineering validation;
- `VALIDATED` or `APPROVED` promotion;
- Project Integrator commits;
- reviewer mutation;
- provider/model execution;
- retry/reflection loops;
- PX4/MAVSDK/Gazebo control;
- any change to the frozen architecture.

Phase 7 owns deterministic engineering trust.
