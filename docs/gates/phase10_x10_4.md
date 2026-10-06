# Phase 10 Gate X10.4 — Path Restriction

Parent: X10.3 GREEN / PHASE 9 GREEN / CP8 GREEN

Status before target evidence: CANDIDATE

## Scope

- Add immutable tool path policy metadata.
- Require X10.2 role/tool authorization before resolving a tool path.
- Bind project paths to the canonical `ProjectStore` project root.
- Validate future workspace roots as trusted, pre-existing strict descendants of `<project>/software` without creating them.
- Accept bounded relative paths only.
- Reject parent traversal, absolute paths, symlinks/broken symlinks, root escape, and arbitrary missing parent creation.
- Keep X10.3 timeout semantics unchanged.
- Do not add file execution, shell/argv access, structured results, audit logging, provider calls, or PX4/MAVSDK access.

## Acceptance

`python3 tools/check_phase10_path_restriction.py`

Expected prefix: `TOOL_PATH_RESTRICTION=PASS`.
