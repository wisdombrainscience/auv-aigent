# Final Pipeline — Remote UAV Platform

## Runtime pipeline

```text
USER
  │
  ▼
Local Web UI
  │
  ├─ target numeric input
  ├─ click-on-map target
  └─ simulation/session controls
  │
  ▼
Session Manager
  │
  ├─ current measured/verified position
  ├─ Home / spawn reference
  ├─ previous run state
  └─ simulation lifecycle
  │
  ▼
Canonical Input + Policy Validation
  │
  ├─ config/experiment.yaml = one region source of truth
  └─ resource guard = timeout + disk quota + free space
  │
  ▼
Pre-flight Gate
  │
  ▼
════════ CAPABILITY EXECUTION WINDOW START ════════
  │
  ▼
GoToTargetModule → UAVAdapter → PX4 SITL/Gazebo
  │
  ├─ telemetry.csv (raw evidence)
  ├─ bounded live telemetry ring buffer → UI
  └─ event/lifecycle evidence
  │
════════ CAPABILITY EXECUTION WINDOW END ═════════
  │
  ▼
STOP recorder + FLUSH + FREEZE dataset
  │
  ▼
Independent streaming verifier
  │
  ├─ final position error
  ├─ timeout
  └─ localization validity
  │
  ▼
PASS / FAIL
  │
  ▼
Evidence Contract Validator
  │
  ▼
SHA256 manifest + read-only finalize
  │
  ├─ SQLite summary/history index
  └─ raw evidence on disk
  │
  ▼
Result UI
  │
  ├─ target
  ├─ actual final position
  ├─ trajectory
  ├─ error / duration / verdict
  ├─ new target → next run starts from actual final
  └─ explicit Land / Restart
```

## AI engineering pipeline

```text
Frozen capability spec
       │
       ├─ Implementation Agent
       ├─ Test Agent
       └─ Recovery Agent
       │
       ▼
Candidate source
       │
       ▼
Deterministic path/symbol boundary
       │
       ▼
Unit/regression tests
       │
       ▼
PX4 SITL runtime pipeline
       │
       ▼
Independent deterministic verifier
```

AI is intentionally outside the real-time control and verdict path.

## Data/resource invariants

1. Runtime RAM must not grow with the number of historical attempts.
2. The authoritative verifier must not load an entire telemetry file into memory.
3. The browser keeps only bounded live trajectory data.
4. Raw telemetry is recorded only during the capability execution window.
5. New runs are blocked before configured disk/free-space limits become unsafe.
6. Reference evidence is never silently rewritten.
7. Post-flight actions must not modify telemetry that already produced a verdict.

## Next framework milestones

Before multi-capability mission composition is considered complete, Takeoff, Land and ReturnHome need their own frozen specs, implementation modules, deterministic verifiers and SITL evidence. They should then reuse the same session, telemetry, evidence, resource and UI infrastructure rather than creating new STEP-specific runtimes.
