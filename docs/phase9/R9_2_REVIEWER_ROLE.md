# R9.2 — Reviewer Role

R9.2 binds Phase-9 review capability to the already-frozen G6.1 MVP role ID
`reviewer`. It does not add a fifth role and does not mutate the role registry.

The capability profile is immutable and exposes one output boundary:
`ReviewResult` (C1.6 / contract version 1.0). The reviewer remains advisory;
`ReviewResult` carries structured issues but contains no PASS/FAIL, approval,
artifact replacement, or mutation authority.

R9.2 may create a logical `AgentInstance` for `reviewer`, reusing the G6.2
identity contract. It does not bind a TaskRecord, context, provider/model,
prompt, tools, execution state, or persistence.

Out of scope: issue allocation/persistence, review task generation (R9.3),
issue owner routing (R9.4), rework tasks (R9.5), max-rework gating (R9.6),
duplicate issue suppression (R9.7), provider execution, artifact mutation,
and UAV control/verification.
