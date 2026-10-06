# G6.7 — Context Manifest

Status: candidate pending target validation.

Scope: bind each persisted `AI-RUN-*` to the exact deterministic `MinimalAgentContext`
used for that run without changing task lifecycle, provider execution, artifact state,
UAV runtime, or verifier authority.

Implementation:
- `ContextManifestStore` persists one immutable JSON manifest per AI-RUN under
  `ai_runs/context_manifests/`.
- Manifest records run/task/role/schema binding, selected fact keys, the minimal-context
  contract version, a SHA-256 of the canonical full context, and its own SHA-256.
- Opening a manifest reopens the AI-RUN and rebuilds the selected context from persisted
  trusted project state; mismatches fail closed.
- G6.6 agent-run SQLite schema remains unchanged at exactly two tables.

Explicit non-goals:
- no provider/model call
- no prompt persistence
- no context documents/review issues/validation failures beyond G6.4 reserved sections
- no DRAFT artifact creation (reserved for G6.8)
- no review/approval/verdict
- no PX4/MAVSDK/UAV command path
