# S3.1 — SQLite Project Schema

S3.1 establishes the minimal SQLite foundation for Project State, which is the frozen source of truth. It does **not** choose the filesystem layout, create/open projects, implement RequirementSet, requirement versions, facts, assumptions, decisions, artifacts, tasks, Agent Engine, or Orchestrator.

The schema version is `1` and contains only three tables:

```text
project_schema_meta
projects
canonical_id_sequences
```

`projects` currently stores only the canonical project identity required by C1.1. Project creation/open semantics remain S3.3. `canonical_id_sequences` seeds every canonical ID kind at sequence `0` but provides no allocator API yet.

SQLite `foreign_keys` is enabled and verified. Both `project_schema_meta.schema_version` and `PRAGMA user_version` must match. Unknown/partial/foreign databases fail closed; there is no silent schema adoption or migration in S3.1.

Schema bootstrap is atomic, but S3.1 does not introduce the general transaction wrapper reserved for S3.9. A reopen/restart test is included because persistence changes require recovery coverage.
