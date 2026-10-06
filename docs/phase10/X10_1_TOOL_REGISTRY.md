# X10.1 — Tool Registry

## Scope

X10.1 introduces only an immutable deterministic registry of logical tool
identifiers defined by the frozen architecture tool/sandbox policy.

Registered tool IDs:

- `project_read`
- `artifact_read`
- `draft_write`
- `calculator`
- `workspace_read`
- `workspace_write`
- `allowlisted_test_runner`
- `issue_create`

The registry stores bounded prompt-free metadata (`tool_id`, `title`,
`purpose`) and exposes lookup helpers. It does not execute tools.

## Frozen boundary

X10.1 deliberately does **not** implement:

- role-specific allowlists (X10.2);
- timeout wrapping (X10.3);
- filesystem path restriction (X10.4);
- structured tool results (X10.5);
- tool audit logging (X10.6);
- arbitrary shell or network execution;
- PX4/MAVSDK access.

The frozen forbidden-by-default capabilities are not entries in the default
registry.

## Failure behavior

Unknown or malformed tool IDs fail closed through deterministic registry
errors. Duplicate registrations and oversized registry definitions are
rejected.

## Persistence

None. The registry is code-defined and immutable. Project State, Task
Registry, Artifact Registry, and review state are unchanged by X10.1.
