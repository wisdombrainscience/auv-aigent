# X10.3 — Timeout Wrapper

X10.3 adds a bounded timeout boundary for trusted logical tool callables after X10.2 role/tool authorization.

## Contract

- `TOOL_TIMEOUT_CONTRACT_VERSION = "1.0"`
- `ToolTimeoutPolicy(timeout_s)` requires a finite positive timeout.
- A tool timeout may not exceed frozen `TASK_TIMEOUT_S = 300` seconds.
- `ToolTimeoutWrapper.run(...)` checks the X10.2 role allowlist before spawning any child.
- Execution occurs in a spawned daemon child process so the wrapper can terminate it at the deadline and so the child cannot spawn nested process trees. Trusted callables must be pickleable/importable for this boundary.
- Timeout is classified through C1.8 as `FailureClass.TOOL / TOOL_TIMEOUT`.

## Deliberate boundaries

X10.3 has no command-string/argv/shell API, executor registry, filesystem path policy, URL/network API, provider binding, PX4/MAVSDK handle, structured tool-result contract, audit persistence, or retry loop.

The callable is supplied by trusted application code, not model output. X10.4 adds path restriction, X10.5 adds structured tool results, and X10.6 adds audit logging.
