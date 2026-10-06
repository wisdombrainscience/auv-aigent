# G6.6 — Agent Run Persistence

G6.6 persists one immutable `AI-RUN-*` record after G6.5 has already produced a
schema-enforced `SchemaEnforcedAgentOutput`.

## Frozen boundary

The persisted record contains only:

```text
run_id
project_id
task_id
role_id
agent_identity
schema_id
result
```

`result` remains the existing `AgentResult` with status `PROPOSED`. Persistence
does not make the proposal reviewed, validated, approved, or authoritative.

## Canonical identity

G6.6 uses the already-frozen canonical ID kind:

```text
AI-RUN-######
```

The sequence is allocated from the existing Project State
`canonical_id_sequences` row for `agent_run`. No new ID namespace is created.
Allocation and insertion of the run record occur in one SQLite transaction.

## Storage

Each project stores run metadata in:

```text
ai_runs/agent_run_registry.sqlite3
```

The registry has exactly two tables:

```text
agent_run_registry_meta
agent_runs
```

Project State remains exactly three tables. This follows the frozen local-first
rule that SQLite owns metadata/state/relationships while the fixed project
filesystem layout provides the `ai_runs/` namespace.

## Integrity and restart behavior

The store:

- rejects symlink/non-file registry paths;
- verifies exact registry tables/version and SQLite `quick_check`;
- stores canonical bounded JSON with duplicate-key/non-finite rejection on read;
- verifies indexed fields against the immutable record;
- reopens the referenced persisted Task→Agent binding and task output schema;
- survives process restart by reopening from the same Project State root;
- rolls back the canonical sequence if insertion fails.

## Explicit non-goals

G6.6 does not:

- call Ollama, MockProvider, Model Manager, or any provider;
- build or persist the G6.7 context manifest;
- create a G6.8 DRAFT artifact;
- review, validate, approve, or produce UAV PASS/FAIL;
- mutate task state, attempt counters, dependencies, scheduler slots, or retries;
- change Project State, Task Registry, or Artifact Registry schemas;
- command PX4/MAVSDK/Gazebo.

G6.7 context manifest is the sole next implementation gate.
