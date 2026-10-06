# X10.4 — Path Restriction

Parent: **X10.3 GREEN / X10.2 GREEN / X10.1 GREEN / PHASE 9 GREEN / CP8 GREEN**.

## Scope

X10.4 adds a deterministic path-policy layer for logical tools. It does not add a file executor.

- Agent-supplied paths are relative-only and bounded.
- Project-scoped tools are anchored to the canonical `ProjectStore` project directory.
- A future workspace-scoped tool must receive a trusted, pre-existing workspace root that is a strict descendant of the project's `software/` directory.
- `..` traversal, absolute paths, symlink/broken-symlink components, root escape, and missing-parent writes fail closed.
- Read policies require an existing target.
- Write policies may address a missing final file only when its parent directory already exists.
- X10.2 authorization is checked before path resolution.

The default path-bearing tools are:

- project: `project_read`, `artifact_read`, `draft_write`
- future isolated workspace: `workspace_read`, `workspace_write`, `allowlisted_test_runner`

`calculator` and `issue_create` are deliberately pathless.

X10.4 does **not** create a Software Agent workspace. The frozen roadmap adds that workspace only after the Tools/Sandbox layer is stable.

## Non-goals

X10.4 does not add shell/argv execution, arbitrary filesystem I/O, network access, executor registration, structured tool results, audit logging, provider calls, or PX4/MAVSDK access.

## Acceptance

```bash
python3 tools/check_phase10_path_restriction.py
```

Expected prefix: `TOOL_PATH_RESTRICTION=PASS`.
