# Phase 10 Gate X10.5 — Structured Tool Results

Parent: X10.4 GREEN / PHASE 9 GREEN / CP8 GREEN.

Acceptance boundary:

- freeze `TOOL_RESULT_CONTRACT_VERSION=1.0`;
- exact five-field `ToolResult` envelope;
- only `SUCCESS` / `FAILURE` statuses;
- strict JSON-compatible success output;
- C1.8 `FailureDescriptor(class=TOOL)` for failures;
- preserve X10.3 `TOOL_TIMEOUT`;
- normalize X10.2 deny and X10.3 invocation/policy failures;
- support deterministic normalization of X10.4 path-policy failures;
- no audit persistence (reserved X10.6);
- no shell/argv, arbitrary network/filesystem access, provider calls, PX4 or raw MAVSDK.

Target evidence required before X10.5 GREEN:

```text
./uavctl test
python3 tools/check_phase10_structured_tool_results.py
```
