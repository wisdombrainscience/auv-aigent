# Phase 10 Gate X10.2 — Role Allowlists

Parent: X10.1 GREEN / PHASE 9 GREEN / CP8 GREEN

Status before target evidence: CANDIDATE

## Scope

- Add immutable `RoleToolAllowlist` and `RoleToolAllowlistRegistry`.
- Cover exactly the four frozen MVP roles.
- Map only registered X10.1 logical tools.
- Use deny-by-default semantics.
- Do not activate `software_engineer`.
- Do not execute tools or introduce X10.3–X10.6 behavior.

## Frozen default mapping

- coordinator: none
- requirements_engineer: artifact_read, draft_write, project_read
- reviewer: artifact_read, issue_create, project_read
- uav_systems_engineer: artifact_read, calculator, draft_write, project_read

## Acceptance

`python3 tools/check_phase10_role_allowlists.py`

Expected prefix: `ROLE_TOOL_ALLOWLISTS=PASS`
