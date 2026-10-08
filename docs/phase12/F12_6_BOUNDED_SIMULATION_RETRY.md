# F12.6 — Bounded Simulation Retry

## Purpose

F12.6 closes the automatic retry portion of the simulation-feedback loop while
preserving the frozen architecture limit:

```text
simulation.max_retry_attempts = 2
```

The original simulation run is not counted as a retry. At most two additional
runtime invocations may occur in one root-run retry chain.

## Required parent state

F12.6 consumes an F12.5 `SimulationReworkTaskGenerationResult`. Automatic retry
is denied unless all of the following remain true:

1. the parent run still has an intact U11.7 linkage record matching project,
   requirement version, exact source lineage, mission/scenario IDs and verifier
   SHA-256;
2. every generated F12.5 rework task has reached `DONE` through the existing
   task state machine;
3. every F12.4 `STALE` simulation source has a newer `APPROVED` and current
   version of the same logical artifact ID;
4. each replacement version was produced by one of the F12.5 rework tasks;
5. the exact approved requirement version used by the failed simulation has not
   changed; and
6. the scenario ID and mission ID remain unchanged.

A requirement or scenario change is not silently treated as a retry. It fails
closed for the later human-gate policy.

## Existing runtime path is reused

F12.6 does not implement a second simulation runtime. The retry proposal is
rebuilt through U11.2 `map_project_to_mission` using the replacement approved
source lineage. Invocation then remains on the existing bridge path:

```text
U11.2 mapping
  → U11.5 authorization
  → U11.6 invoke_existing_runtime
  → existing MissionExperimentRunner
  → independent mission verifier
  → U11.7 link_uav_run_to_project
```

The caller supplies the trusted runtime adapter. F12.6 does not instantiate PX4,
MAVSDK, or any vehicle adapter itself.

## Retry-chain persistence

The retry budget must survive process restart. F12.6 therefore adds one
project-local registry:

```text
<project>/simulation/simulation_retries.sqlite3
```

It has exactly two tables: registry metadata and bounded retry attempts. The
canonical Project State database remains unchanged at three tables.

Each attempt is first persisted as `RESERVED` before runtime invocation. A
successful U11.7 linkage changes it to `LINKED`; an invocation/linkage exception
changes it to `FAILED`. Both `FAILED` attempts and crash-surviving `RESERVED`
attempts consume/hold budget so a process crash cannot reopen an unbounded retry
loop.

A completed retry called again with the exact same request is restart-idempotent:
the existing U11.7-linked retry is returned without invoking the runtime again.

## Root-run budget

Retry counting follows the root run across successive verifier-fail → rework →
retry cycles. If retry 1 itself fails verification and generates another F12.5
rework event, the next invocation is retry 2 of the same root chain. A further
retry request raises `SimulationRetryBudgetExhaustedError` before runtime
invocation.

F12.6 does **not** transition any task or project to `NEEDS_HUMAN`; F12.7 owns
that transition.

## Verifier authority

F12.6 never parses or rewrites the new retry verdict. Its result preserves the
new U11.7-linked run ID, replacement source lineage and verifier evidence hash,
with separate `SIMULATION_RESULT` and `VERIFIER_RESULT` provenance. The next
feedback cycle begins again at F12.1 if the retry result must be interpreted.

## Not in F12.6

F12.6 deliberately does not:

- allocate a canonical ISSUE ID;
- execute engineering rework tasks;
- bypass U11.2/U11.5/U11.6/U11.7;
- create or access raw MAVSDK/PX4 objects;
- reinterpret independent verifier PASS/FAIL;
- transition to `NEEDS_HUMAN` — F12.7;
- mark Phase 12 or CP10 GREEN.
