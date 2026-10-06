# U11.3-H1 — Scenario Path Hardening

This is a narrow security hotfix to the already-implemented U11.3 bridge-validation boundary.

The existing mission runtime constructs evidence paths using `scenario_id`. The original U11.1 structural contract accepted clean text but did not make a runtime-path safety assertion, so U11.3 now requires the scenario identifier to be exactly one portable filesystem-safe path segment before authorization/runtime invocation can pass.

Accepted characters are ASCII letters, digits, `_`, `-`, and `.` with a non-dot first character. Path separators, traversal segments, whitespace/control text, and absolute/nested paths fail closed.

The hotfix does not alter the U11.1 wire schema, capability or mission allowlists, runtime implementation, verifier authority, persistence, retry behavior, PX4 access, or MAVSDK access. U11.6 already re-runs the U11.5→U11.4→U11.3 chain before runtime construction, so unsafe scenarios are denied before any runtime/evidence object is created.
