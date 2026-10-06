# C1.8 — Failure Taxonomy

Status: candidate for target gate.

## Frozen failure classes

C1.8 freezes the failure domains required by the architecture:

- `INFRA`
- `PROVIDER`
- `MODEL`
- `TOOL`
- `PLAN`
- `TASK`
- `REVIEW`
- `VALIDATION`
- `SIMULATION`
- `VERIFICATION`
- `USER_INPUT`

These classes prevent unrelated failures from collapsing into one generic
exception path.

Examples include:

- `PROVIDER_UNAVAILABLE`
- `MODEL_INVALID_OUTPUT`
- `TOOL_TIMEOUT`
- `PLAN_INVALID`
- `TASK_TIMEOUT`
- `REVIEW_REWORK_EXHAUSTED`
- `VALIDATION_FAILED`
- `SIMULATION_FAILED`
- `VERIFICATION_FAILED`
- `USER_INPUT_REQUIRED`

The detailed code vocabulary is deliberately extensible. A failure code must be
uppercase, machine-readable, and prefixed by its canonical failure class.

## Minimum structured failure

```json
{
  "class": "MODEL",
  "code": "MODEL_INVALID_OUTPUT",
  "message": "Structured model output did not satisfy the contract.",
  "details": {
    "contract": "AgentResult",
    "attempt": 1
  }
}
```

`details` is immutable strict JSON and rejects NaN/Infinity.

## Critical bounded-retry boundary

A C1.8 failure describes **what happened**. It does not authorize another
attempt.

Therefore the C1.8 descriptor intentionally has no:

- retry boolean;
- retry count;
- next task state;
- automatic recovery action;
- PASS/FAIL simulation verdict.

The later Task Engine and Orchestrator own bounded retry/rework policy under the
frozen limits. Exhausted policy transitions to `NEEDS_HUMAN`; C1.8 does not
implement that transition itself.

This separation prevents an agent/provider/tool error from silently creating an
unbounded retry loop.

## Simulation vs verification

`SIMULATION` and `VERIFICATION` are deliberately separate.

Simulation execution/environment failure is not the same as an independent
verifier result. The existing deterministic UAV verifier remains the sole
simulation/capability/mission PASS/FAIL authority.

## Deliberate boundaries

C1.8 does not implement exception mapping, retry counters, persistence,
scheduler policy, provider execution, Ollama, Agent Engine, review/rework
execution, orchestration, RAG, or UAV Bridge.

Those remain later gated work packages.
