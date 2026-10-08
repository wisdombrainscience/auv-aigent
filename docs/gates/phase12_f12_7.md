# Phase 12 / F12.7 Gate — NEEDS_HUMAN Transition

Status in this package: **CANDIDATE** pending target evidence.

F12.7 is GREEN only after the exact F12.6-GREEN parent applies this package,
the complete target regression suite passes, and
`tools/check_phase12_simulation_needs_human.py` reports
`SIMULATION_NEEDS_HUMAN=PASS`.

Acceptance requires:

1. verifier PASS produces no human gate;
2. legitimate F12.5 work still in progress remains automation-eligible;
3. valid retry preconditions with remaining budget remain automation-eligible;
4. two consumed F12.6 retry attempts route to typed `NEEDS_HUMAN`;
5. crash-surviving `RESERVED` retry state fails closed to `NEEDS_HUMAN`;
6. completed rework with invalid F12.6 retry preconditions routes to
   `NEEDS_HUMAN`;
7. existing task-level `NEEDS_HUMAN` is preserved;
8. no task/project/artifact/retry/UAV-link mutation is performed by F12.7;
9. `max_retry_attempts=2`, verifier authority, and `VERIFIER_RESULT` provenance
   remain unchanged;
10. no provider/model call, direct PX4/MAVSDK construction, or extra runtime
    invocation occurs.

The F12.7 human gate is a typed deterministic outcome, following the established
R9.6 precedent, rather than an illegal forced lifecycle edge such as
`DONE -> NEEDS_HUMAN`.

When the target proves this gate plus the full regression suite, Phase 12 and
CP10 may be marked GREEN.
