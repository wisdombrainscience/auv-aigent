# G6.5 — Schema-Enforced Output

G6.5 adds deterministic schema enforcement for one Phase-6 agent task. It does
not call a provider/model and does not execute a task. It validates a completed
provider-neutral `GenerateResponse` supplied by a later execution layer.

## Source of truth

The persisted task contract remains authoritative:

```text
TaskRecord.output_schema
        ↓
OutputSchemaRegistry
        ↓
strict provider-facing JSON schema
        +
post-generation contract parser
        ↓
SchemaEnforcedAgentOutput
```

G6.5 activates exactly one output schema:

```text
engineering_task.v1 → AgentResult
```

Unknown schema IDs fail closed. There is no fallback to the generic schema.
This preserves the future PlanValidator requirement that task schemas be known
before a plan is executable.

## Double enforcement

`provider_schema_for_task()` returns a detached strict JSON-schema snapshot that
can later be supplied through the existing `GenerateRequest.output_schema`
provider contract.

After generation, G6.5 always reuses the existing Phase-2
`parse_agent_result_response()` boundary. Provider-side structured generation
is therefore not trusted by itself. Post-generation validation still rejects:

- oversized content;
- invalid/non-standard JSON;
- duplicate JSON keys;
- non-object top-level output;
- wrong/missing/extra AgentResult fields;
- invalid provenance classification;
- non-finite/non-JSON nested values.

These failures retain the existing structured taxonomy:

```text
class = MODEL
code  = MODEL_INVALID_OUTPUT
```

G6.5 does not create a second competing model-output failure contract.

## Authority

A valid result is always:

```text
status = PROPOSED
```

It is not REVIEWED, VALIDATED, APPROVED, a simulation result, or verifier
verdict. Schema validity does not grant engineering correctness or UAV
authority.

## Bounds

```text
MAX_OUTPUT_SCHEMA_ID_CHARS = 128
MAX_OUTPUT_SCHEMA_BYTES    = 64 KiB
```

Schema snapshots are immutable internally and detached when returned to callers.
Bounds fail closed; schemas are never truncated.

## Preserved boundaries

G6.5 does not:

- call Ollama/ModelManager/provider;
- create retries or reflection loops;
- mutate task status, attempt, dependencies, or scheduler state;
- persist an `AI-RUN-*` record — G6.6;
- persist a context manifest — G6.7;
- create a DRAFT artifact — G6.8;
- review, validate, approve, simulate, verify, or command UAV execution.

G6.6 Agent run persistence is the sole next step after target validation.
