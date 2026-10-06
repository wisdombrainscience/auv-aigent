# X10.5 — Structured Tool Results

X10.5 freezes a single immutable result envelope at the bounded tool boundary.
It does not add tool executors, shell/argv execution, filesystem I/O, network
access, provider access, PX4/MAVSDK access, retries, task transitions, or audit
persistence.

The X10.5 wire envelope contains exactly five fields:

```text
status
role_id
tool_id
output
failure
```

`status` is exactly `SUCCESS` or `FAILURE`.

- `SUCCESS` requires `failure=null`; `output` must be strict JSON-compatible.
- `FAILURE` requires `output=null` and one C1.8 `FailureDescriptor` whose class is
  exactly `TOOL`.

The structured runner composes X10.2 authorization with X10.3 bounded execution.
It preserves `TOOL_TIMEOUT`, normalizes deny/invocation/policy errors, and
converts non-JSON tool output to `TOOL_RESULT_INVALID` instead of allowing an
arbitrary Python object to cross the result boundary.

X10.4 path-policy failures can be normalized through the same envelope without
copying host path text into the result. Path authorization/resolution remains an
X10.4 responsibility; X10.5 does not open files.

Stable X10.5 failure codes are:

```text
TOOL_NOT_ALLOWED
TOOL_POLICY_INVALID
TOOL_INVOCATION_FAILED
TOOL_PATH_NOT_APPLICABLE
TOOL_PATH_DENIED
TOOL_PATH_INVALID
TOOL_RESULT_INVALID
```

`TOOL_TIMEOUT` remains the X10.3 code and is preserved unchanged.

Audit timestamps, log IDs, persistence, and durable execution history remain
reserved for X10.6.
