# F12.7 — NEEDS_HUMAN Transition

## Purpose

F12.7 closes the bounded simulation-feedback loop with a deterministic human
handoff when automatic recovery can no longer continue safely. It does not add
another retry path and it does not reinterpret the independent mission verifier.

The frozen Phase-12 order remains:

```text
F12.1 verifier-result parser
  → F12.2 simulation issue taxonomy
  → F12.3 issue → engineering role mapping
  → F12.4 lineage invalidation
  → F12.5 rework task generation
  → F12.6 bounded simulation retry
  → F12.7 NEEDS_HUMAN transition
```

## Human-gate semantics

The existing task state graph does not permit arbitrary transitions from a
terminal task such as `DONE` to `NEEDS_HUMAN`. F12.5 rework tasks may already
be `DONE` when the second simulation retry has been consumed. Therefore F12.7
follows the same architectural precedent as R9.6: it returns a **typed,
deterministic `NEEDS_HUMAN` gate outcome** rather than mutating an unrelated
TaskRecord through an illegal edge or inventing a new project-status schema.

`SimulationHumanGateDecision.target_status` is `TaskState.NEEDS_HUMAN` only
when human intervention is required. The gate itself is read-only.

## Outcomes

F12.7 distinguishes continuing automation from a required human handoff.

No human gate is produced when:

- the verifier result is PASS (`VERIFIER_PASS`);
- generated F12.5 work is still legitimately in progress (`REWORK_IN_PROGRESS`);
- all F12.5 tasks are DONE, F12.6 preconditions remain valid, and retry budget
  remains (`AUTO_RETRY_AVAILABLE`).

`NEEDS_HUMAN` is produced when:

- an F12.5 rework task has already entered `NEEDS_HUMAN`;
- a required rework task terminates as `FAILED` or `CANCELLED`;
- the root simulation retry chain has consumed both frozen retries;
- a crash-surviving F12.6 `RESERVED` attempt makes automatic continuation
  ambiguous and therefore fail-closed;
- all rework tasks are DONE but F12.6 retry preconditions are no longer valid,
  including missing/invalid replacement lineage or requirement/scenario drift;
- the retry registry is invalid; or
- the parent U11.7 run linkage is invalid.

## Frozen retry limit

F12.7 consumes the existing F12.6 registry read-only and preserves:

```text
simulation.max_retry_attempts = 2
```

No third automatic retry is authorized. A F12.6 `FAILED` attempt consumes its
attempt number but, while the two-retry budget still has room and all retry
preconditions remain valid, the gate may still report `AUTO_RETRY_AVAILABLE`.
A `RESERVED` attempt is different: it represents an uncertain crash boundary,
so F12.7 fails closed to `NEEDS_HUMAN` rather than risking a duplicate runtime
invocation.

## No new persistence or authority

F12.7 adds no database and writes no Project State, task, artifact, retry, or
UAV-linkage record. It preserves `VERIFIER_RESULT` provenance and the
independent mission verifier remains the only authoritative simulation verdict.

F12.7 does not:

- execute F12.5 tasks;
- alter task attempts;
- create a third retry;
- call a local AI provider/model;
- instantiate PX4/MAVSDK or a simulation adapter;
- mutate artifact lineage;
- allocate ISSUE IDs;
- rewrite verifier PASS/FAIL.

## Phase / checkpoint readiness

With F12.1–F12.7 active and all acceptance probes passing, the implementation is
**READY** for Phase 12 / CP10 closure. Phase 12 and CP10 become GREEN only after
the exact-parent package is applied on the target, the full target regression
suite passes, and `SIMULATION_NEEDS_HUMAN=PASS` is observed there.
