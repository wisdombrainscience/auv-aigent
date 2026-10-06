# X10.6 — Audit Log

X10.6 completes the Phase 10 Tools/Sandbox foundation by adding a project-local audit trail around the X10.5 structured tool-result boundary.

The log is stored under the existing `ai_runs/` project directory as `tool_audit.jsonl`. Each append records a contiguous local sequence, project binding, role, tool, and the unchanged X10.5 `ToolResult`. Success, authorization denial, timeout, invocation failure, and other structured X10.5 outcomes can therefore be audited without changing the five-field result contract.

The implementation rejects symbolic-link log targets, corrupt/partial JSONL records, sequence discontinuities, project mismatches, and role/tool/result binding mismatches. Appends are process-locked and fsynced.

X10.6 adds no shell/argv API, executor registry, arbitrary filesystem/network access, provider calls, PX4/MAVSDK access, Software Agent workspace, or UAV Bridge behavior.
