# Remote UAV Evaluation v1.6.1 — Verification Protocol and Reproducibility Record

## 1. Purpose

This document records the evaluation code, experimental protocol, result-classification rules, environment controls, known infrastructure failure modes, and reproducibility rules used to verify the Remote UAV PX4 SITL/Gazebo baseline.

The evaluation layer is intentionally separated from the main product branch. It must not change the semantics of the capability verifier simply to improve benchmark results.

## 2. Scope

- Scope: `SIMULATION_ONLY`
- Simulator: PX4 SITL + Gazebo, x500
- Flight interface: MAVSDK through the project UAV adapter
- Internal coordinate frame: `LOCAL_NED`
- Verdict authority: independent Python verifier over frozen telemetry/evidence
- Capability `completed` is not automatically equivalent to verifier `PASS`
- Baseline capabilities: `TAKEOFF-001`, `NAV-001`, `RETURN-HOME-001`, `LAND-001`

## 3. Frozen evaluation baseline

Evaluation baseline: **v1.6.1**

Frozen source archive SHA256:

```text
0a0439bd0ca672b28d0f17812b0b57b2877364cb8b22c4746f2ac9d70b967342
```

Final full regression recorded before the official campaign:

```text
3028 passed
```

Evaluation runtime configuration:

```text
UAV_DATA_DIR="$HOME/UAV/remote_uav_official_v1_6_1"
UAV_SIM_BACKEND="gazebo"
UAV_PORT="8470"
UAV_SIM_HOST_LOG_MODE="docker"
```

Do not modify evaluation semantics after the baseline has been frozen. A code change after freeze requires a new evaluation baseline/version.

## 4. Why the evaluation harness was hardened

### 4.1 Unbounded simulator host log

An early longitudinal smoke test exposed host-side simulator logging growth. `runs/.runtime/simulator.log` reached approximately 5.6 GB and exhausted free disk space.

The root cause was that PX4/Gazebo stdout was mirrored into a host file independently from Docker log rotation.

Mitigation:

```text
UAV_SIM_HOST_LOG_MODE="docker"
```

The Docker runtime keeps bounded logs; the evaluation host file is not allowed to grow without bound.

### 4.2 Simulator running is not the same as flight-ready

Another failure mode occurred when the container was running, MAVSDK was connected, and local position could already be valid, but PX4 still rejected `arm()` with `COMMAND_DENIED`.

The evaluation therefore added a flight-readiness gate. A capability sample starts only after the PX4 health state is sufficiently ready and stable.

Typical required state:

```text
connected = true
localization_valid = true
health_local_position_ok = true
health_global_position_ok = true
health_home_position_ok = true
health_is_armable = true
stable_s >= 2
```

A pre-execution readiness failure is an **infrastructure exclusion**, not a capability failure.

### 4.3 Lifecycle cleanup

The evaluation owns the simulator instances it starts. On completion/failure, owned simulator resources must be stopped so that later samples do not inherit stale PX4/Gazebo state.

## 5. Result classification

For a capability sample that actually enters execution:

| Module state | Independent verifier | Classification |
|---|---|---|
| completed | PASS | `AGREE_PASS` |
| failed | FAIL | `AGREE_FAIL` |
| completed | FAIL | `SILENT_FAILURE` |
| failed | PASS | `VERIFIER_ONLY_LENIENT` |

A readiness/setup failure before capability execution is reported separately as infrastructure failure/exclusion and is not counted in capability `N`.

## 6. Evidence discipline

Each valid attempt should retain enough evidence to reconstruct the verdict. Depending on the code path, this can include:

- raw user/request input
- normalized/canonical input
- validation result
- boundary/precondition result
- metadata
- event stream
- telemetry CSV
- module output
- independent verification
- evidence-contract result
- runtime error, when applicable
- finalization marker

The verifier must consume frozen evidence. Runtime/UI state is not verdict authority.

## 7. Repository isolation rules

The evaluation work should live on a dedicated Git branch and preferably a separate Git worktree.

Recommended branch:

```text
evaluation/v1.6.1-verification
```

Do not use `git reset --hard`, `git clean -fd`, or copy the evaluation tree directly into an active dirty main working tree.

Do not commit `.venv/`, `runs/`, `uav.env`, runtime caches, transient `reports/evaluation/` datasets, large simulator logs, or local credentials.

Commit the evaluation code, regression tests, protocol, manifests, and compact result summaries instead.

## 8. Preflight before an experiment

Example:

```bash
./uavctl doctor
docker ps
df -h /
```

Recommended conditions:

- simulator image already present
- no unrelated Docker workload consuming CPU/RAM/I/O
- enough free disk headroom
- official data root separated from smoke/debug data
- source/hash baseline verified
- evaluation branch clean

## 9. Regression gate

Run the full test suite before freezing or publishing an official evaluation baseline:

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 .venv/bin/python -m pytest -q
```

The recorded v1.6.1 baseline result was:

```text
3028 passed
```

## 10. Comparative evaluation

### 10.1 Objective

Measure agreement between capability execution status and independent verification across complete mission cycles.

### 10.2 Official command

```bash
UAV_GAZEBO_GUI=off ./evaluation-uav comparative --cycles 30
```

### 10.3 Official primary result currently recorded

Requested cycles: `30`

Valid cycles that passed infrastructure readiness: `25`

Infrastructure-excluded cycles: `5`

Valid capability executions: `25 × 4 = 100`

| Capability | N | Agree-P | Agree-F | Silent failure | V-only lenient |
|---|---:|---:|---:|---:|---:|
| TAKEOFF-001 | 25 | 25 | 0 | 0 | 0 |
| NAV-001 | 25 | 25 | 0 | 0 | 0 |
| RETURN-HOME-001 | 25 | 25 | 0 | 0 | 0 |
| LAND-001 | 25 | 25 | 0 | 0 | 0 |

Interpretation:

- capability agreement among valid executed samples: 100/100 `AGREE_PASS`
- infrastructure completion: 25/30 cycles
- do **not** report this as "30/30 mission PASS"
- do **not** silently run five replacement cycles and merge them into the primary dataset

The five pre-execution readiness failures are part of the official infrastructure result.

## 11. Reliability / fault-injection evaluation

### 11.1 Conditions

1. Fresh TAKEOFF — near target
2. Fresh TAKEOFF — far target
3. CONTINUE-chained navigation
4. Injected telemetry dropout
5. Injected timeout

### 11.2 Expected behavior

| Condition | Expected result |
|---|---|
| Fresh near | verifier PASS |
| Fresh far | verifier PASS |
| CONTINUE-chained | verifier PASS |
| Injected telemetry dropout | verifier FAIL |
| Injected timeout | module FAIL + verifier FAIL |

Fault-injection experiments are evaluated using **expected-behavior rate**, not ordinary capability pass rate.

A telemetry-dropout sample may have:

```text
module=completed
verifier=FAIL
```

This is expected and demonstrates fail-closed independent verification.

### 11.3 Infrastructure exclusions

A failure before capability execution is logged separately and does not reduce requested capability sample count. The harness retries the same requested sample within the configured infrastructure-exclusion budget.

Recommended official campaign:

```bash
UAV_GAZEBO_GUI=off ./evaluation-uav reliability   --trials 30   --fault-trials 30   --continue-flights 30   --max-infra-exclusions-per-condition 6   --base-url http://127.0.0.1:8470
```

Do not increase the exclusion budget after the campaign begins.

## 12. Longitudinal resource evaluation

### 12.1 Objective

Observe long-duration runtime/resource behavior rather than only navigation correctness.

Metrics:

- RSS
- open file descriptors
- residual MAVSDK processes
- evidence/storage growth
- simulator host-log growth
- API latency
- valid/failed flight count

### 12.2 Official command

```bash
UAV_GAZEBO_GUI=off ./evaluation-uav longitudinal   --hours 4   --base-url http://127.0.0.1:8470
```

Target reporting checkpoints:

```text
0 min
60 min
120 min
240 min
```

### 12.3 Smoke evidence already observed

A short smoke run (~3 minutes) recorded:

```text
26/26 NAV verifier PASS
FD remained constant at 6
residual MAVSDK process count = 0
host simulator log ~= 0 MB
storage growth remained small
```

This smoke result validates the measurement path but is **not** a substitute for the >=4 h official longitudinal campaign.

## 13. Dataset separation

Use separate roots for debug/smoke data, official comparative data, official reliability data, and official longitudinal data.

Do not aggregate historical smoke runs into official statistics.

A result directory should be identified by its exact timestamp/path and, where practical, hashed after completion.

## 14. Statistical reporting

For binomial PASS/agreement/detection rates, report:

- `n`
- observed rate
- Wilson 95% confidence interval

For position error, report where available:

- mean
- standard deviation
- median
- p95
- maximum

Do not turn a smoke sample into a final reliability claim.

## 15. Failure-preservation rule

Official campaigns must preserve failures.

Do not:

- delete failed samples
- rerun only failed samples and replace them
- modify thresholds after seeing results
- patch the code midway through the same official campaign
- merge replacement samples into the original primary dataset without explicitly labeling a replication campaign

If a frozen baseline needs a code fix, create a new baseline version and rerun the campaign as a new experiment.

## 16. Known current finding

> Once PX4 reaches flight-ready state, the evaluated capability layer and independent verifier showed complete agreement across 100 valid capability executions (25 complete mission cycles). However, PX4 SITL cold-start/arming readiness remained a material infrastructure limitation: 5 of 30 requested cycles did not reach flight readiness within the bounded retry policy.

This distinction must be maintained in reports.

## 17. Suggested Git layout

```text
evaluation/
  direct_runtime.py
  run_comparative.py
  run_reliability.py
  run_longitudinal.py
  ...

evaluation-uav

tests/unit/
  test_evaluation_bounded_simulator_log.py
  test_evaluation_startup_readiness.py
  test_evaluation_reliability_readiness.py
  test_evaluation_reliability_v16.py

docs/evaluation/
  REMOTE_UAV_EVALUATION_V1_6_1_PROTOCOL.md
  RESULTS_CURRENT.md
  BASELINE_MANIFEST.md
```

The exact set of evaluation files should match the frozen v1.6.1 source used to generate the official campaign.

## 18. Review checklist before merge

The evaluation branch should normally remain separate from `main` unless there is an explicit decision to integrate it.

Before any merge:

- compare evaluation branch against `main`
- review the core simulator-supervisor changes separately
- run full regression
- verify no runtime data or credentials are committed
- verify protocol/result documents match actual evidence
- require pull-request review

The evaluation branch itself can be pushed and reviewed without changing the main branch.
