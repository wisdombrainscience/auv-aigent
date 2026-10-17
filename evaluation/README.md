# Remote UAV Evaluation Pack v1

This pack fills the three evaluation sections requested by the project lead using **real PX4 SITL/Gazebo runs** and frozen evidence.

It does not change capability, verifier, AI-agent, or mission semantics. It only adds evaluation tooling.

## Output mapping

1. **Comparative evaluation**: module completion vs independent verifier for TAKEOFF-001, NAV-001, RETURN-HOME-001, LAND-001. Four mutually exclusive cells are recorded: `Agree-P`, `Agree-F`, `Silent fail.` (module completed, verifier FAIL), and `V-only lenient` (module not completed, verifier PASS).
2. **Reliability evaluation**: Fresh TAKEOFF + near targets, Fresh TAKEOFF + far targets, CONTINUE-chained, injected telemetry dropout, injected timeout. Wilson 95% confidence intervals and position-error statistics are computed without SciPy.
3. **Longitudinal resource evaluation**: duration-based soak (default 4 h), recording RSS, file descriptors, residual `mavsdk_server` count, evidence storage, and API latency. Milestones include 0, 60, 120, and 240 min.

For fault-injection conditions, a raw verifier PASS rate is still shown, but **expected-behavior rate** is also reported. Telemetry dropout and forced timeout are expected to fail closed, so a verifier FAIL is the correct behavior for those conditions.

## Important baseline note

Each result directory contains `source_manifest.json`. It records Git branch/head, dirty-worktree status, and SHA256 hashes of critical runtime/verifier files. This is important when the working tree contains uncommitted Phase 13 changes: the Git commit alone is not enough to identify the evaluated source snapshot.

## Recommended campaign

Use a desktop terminal with Docker/PX4/Gazebo available and the project virtual environment installed.

### A. Comparative

Quick smoke:

```bash
./evaluation-uav comparative --cycles 2
```

Report-quality run:

```bash
./evaluation-uav comparative --cycles 30
```

Each cycle runs a real Takeoff -> NAV -> Return Home -> Land mission on a fresh simulation session. A failure early in a cycle may reduce N for later capabilities; this is retained rather than hidden.

### B. Reliability

`CONTINUE-chained` uses the real localhost Web API, so first start the console in Terminal 1:

```bash
UAV_GAZEBO_GUI=off ./start-uav
```

Then in Terminal 2 run a smoke campaign:

```bash
./evaluation-uav reliability --trials 2 --fault-trials 2 --continue-flights 4
```

Recommended report-quality campaign:

```bash
./evaluation-uav reliability --trials 30 --fault-trials 20 --continue-flights 50
```

The injected telemetry-dropout trial uses the real PX4 flight path but an evaluation-only adapter proxy marks a bounded subset of evidence samples as `localization_valid=false`. Flight-control methods are not altered. This specifically tests that the independent verifier fails closed on corrupted measured evidence.

The injected-timeout trial uses a real far-target NAV with a deliberately short timeout (default 0.5 s).

### C. Longitudinal soak >= 4 h

Keep Terminal 1 running:

```bash
UAV_GAZEBO_GUI=off ./start-uav
```

Terminal 2:

```bash
./evaluation-uav longitudinal --hours 4
```

Do not suspend the machine. Avoid unrelated high-load jobs while this test runs. The tool stops and marks the run FAIL if a flight is non-PASS, the session becomes RESET_REQUIRED, or the resource guard becomes unhealthy.

### D. Produce leader-ready tables

After all three result sets exist:

```bash
./evaluation-uav aggregate
```

This creates:

```text
reports/evaluation/leader_tables_<timestamp>/LEADER_EVALUATION_TABLES.md
```

When sending results back for report writing, zip the entire `reports/evaluation/` directory so raw CSV/JSON summaries and source/environment manifests remain traceable.


## v1.1 runtime notes

- Comparative startup tolerates a transient slow MAVSDK `position` stream after simulator restart and retries within the overall readiness deadline.
- Longitudinal auto-starts `start-uav` on the requested localhost port when no console is already listening. Use `--no-auto-start-console` only when you intentionally manage the console yourself.
- Always run a short longitudinal smoke such as `--hours 0.03` before the 4 h campaign.
