# G6.4 — Minimal Context Builder

G6.4 introduces the first bounded, structured context builder for one mapped
Phase-6 agent task. It does not assemble a free-form prompt and does not call a
provider/model.

## Frozen policy hierarchy

The builder preserves the frozen hierarchy structurally:

```text
SYSTEM POLICY
      >
ROLE POLICY
      >
TASK CONTRACT
      >
APPROVED PROJECT STATE
      >
RETRIEVED DOCUMENT CONTENT
```

System policy remains runtime-owned and is not copied into project data.
G6.4 includes G6.1 role policy metadata and the persisted TaskRecord as separate
trusted sections.

## Relevant approved project state

G6.4 does not send all project history. It selects only explicit task inputs:

- requirement references in `TaskRecord.input_artifacts` are opened at the
  exact referenced RequirementSet version and must be `APPROVED`;
- artifact references in `TaskRecord.input_artifacts` are opened at the exact
  version and must be `APPROVED`;
- parent artifact content must pass the existing content-hash verification and
  must be bounded UTF-8 text for this minimal implementation;
- project facts are included only when the caller explicitly supplies bounded
  `fact_keys`.

Draft/rejected/stale/unapproved inputs are never silently promoted into model
context.

## Bounds

```text
MAX_CONTEXT_FACT_KEYS              = 64
MAX_CONTEXT_ARTIFACT_TEXT_BYTES    = 256 KiB per artifact
MAX_MINIMAL_CONTEXT_BYTES          = 1 MiB total canonical payload
```

Limits fail closed; content is not truncated.

## Reserved sections

The frozen architecture also allows previous review issues, previous
deterministic validation failures, and explicitly retrieved project documents.
Those sources are not yet wired by G6.4 and are represented as reserved empty
sections rather than fabricated data. RAG/document retrieval remains deferred.

## Boundaries

G6.4 does not invoke Ollama/ModelManager, render a system prompt, execute a task,
change Task State, persist Agent runs, create artifacts, or command PX4/Gazebo.
G6.5 owns schema-enforced model output. G6.7 owns the persistent context
manifest.
