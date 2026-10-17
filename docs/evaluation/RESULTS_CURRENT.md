# Remote UAV Evaluation v1.6.1 — Current Results

## Frozen baseline

- Baseline: `v1.6.1`
- Frozen source SHA256: `0a0439bd0ca672b28d0f17812b0b57b2877364cb8b22c4746f2ac9d70b967342`
- Final full regression before official campaign: `3028 passed`
- Scope: `SIMULATION_ONLY`
- Verdict authority: independent verifier over frozen telemetry/evidence

## Official Comparative — Primary Campaign

Requested cycles: `30`

Valid cycles that reached flight readiness and executed the full capability sequence: `25`

Pre-execution infrastructure exclusions: `5`

Valid capability executions: `100`

| Capability | N | Agree-P | Agree-F | Silent failure | V-only lenient |
|---|---:|---:|---:|---:|---:|
| TAKEOFF-001 | 25 | 25 | 0 | 0 | 0 |
| NAV-001 | 25 | 25 | 0 | 0 | 0 |
| RETURN-HOME-001 | 25 | 25 | 0 | 0 | 0 |
| LAND-001 | 25 | 25 | 0 | 0 | 0 |

Interpretation:

- Valid executed capability samples: `100/100 AGREE_PASS`
- Infrastructure completion: `25/30 = 83.3%`
- The campaign must **not** be represented as `30/30 mission PASS`.
- The five excluded cycles failed before capability execution because PX4 did not reach bounded flight-readiness.
- These exclusions remain part of the official infrastructure finding and must not be replaced silently.

## Reliability smoke — v1.6.1

Observed smoke protocol:

- Fresh TAKEOFF near: requested 2, valid n=2, 2/2 verifier PASS
- Fresh TAKEOFF far: requested 2, valid n=2, 2/2 verifier PASS; 1 infrastructure exclusion before valid sample completion
- CONTINUE-chained: requested 4, valid n=4, 4/4 verifier PASS
- Injected telemetry dropout: requested 2, valid n=2, 2/2 verifier FAIL as expected
- Injected timeout: requested 2, valid n=2, module FAIL + verifier FAIL as expected
- Overall smoke result: `RELIABILITY_RESULT=PASS`

This is a protocol/harness smoke result, **not** the final n=30 reliability campaign.

## Longitudinal smoke

Short smoke duration: approximately 3 minutes.

Observed:

- NAV verifier PASS: `26/26`
- FD count remained constant at `6`
- residual `mavsdk_server` process count: `0`
- host simulator log: approximately `0 MB` after bounded-log hardening
- storage growth remained small
- RSS increased during initial warm-up; a >=4 h run is still required before making a long-duration memory-stability claim

## Evaluation hardening findings

### Unbounded simulator host logging

An earlier smoke exposed `runs/.runtime/simulator.log` growth to approximately `5.6 GB`, exhausting free disk space. The frozen evaluation uses Docker-managed logging mode instead of unbounded host mirroring.

### PX4 flight-readiness race

An earlier setup path could report simulation running/MAVSDK connected while PX4 still rejected `arm()` with `COMMAND_DENIED`. The evaluation added a bounded readiness gate and separates pre-execution readiness failures from capability failures.

## Pending official work

- Reliability: n=30 for each of five conditions
- Longitudinal: >=4 h, checkpoints at 0/60/120/240 min
