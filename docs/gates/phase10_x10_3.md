# Phase 10 Gate X10.3 — Timeout Wrapper

Parent: X10.2 GREEN / PHASE 9 GREEN / CP8 GREEN

Status before target evidence: CANDIDATE

## Scope

- Add deterministic finite `ToolTimeoutPolicy`.
- Enforce X10.2 role/tool authorization before execution.
- Run trusted logical tool callables in a killable spawned daemon child process.
- Terminate/kill overdue child work and classify timeout as C1.8 `TOOL_TIMEOUT`.
- Bound timeout by frozen `TASK_TIMEOUT_S=300` seconds.
- Do not add shell/command execution, executor registry, path policy, structured results, audit logging, provider calls, or PX4/MAVSDK access.

## Acceptance

`python3 tools/check_phase10_timeout_wrapper.py`

Expected prefix: `TOOL_TIMEOUT_WRAPPER=PASS`
