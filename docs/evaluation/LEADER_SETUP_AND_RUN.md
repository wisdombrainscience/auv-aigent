# Remote UAV Evaluation v1.6.1 — Leader Setup & Run Guide

This guide is for a reviewer/leader who wants to clone, install, run, and reproduce the
Remote UAV evaluation branch without modifying `main`.

## 1. Branch and scope

Repository:

```text
wisdombrainscience/auv-aigent
```

Evaluation branch:

```text
evaluation/v1.6.1-verification
```

Current reviewed branch commit at the time this guide was written:

```text
93a3033
```

Base project commit:

```text
1f55ba713826578bd51a3d7d474b85a6c3a64f55
```

Scope:

```text
SIMULATION_ONLY
```

Verdict authority:

```text
Independent Python verifier over frozen telemetry/evidence
```

Do not merge the Draft PR into `main` merely to run the evaluation. The branch can be
cloned and executed independently.

---

## 2. Reference environment

The campaign was developed and exercised on a Linux x86_64 workstation with:

- Ubuntu 24.04-class environment
- Python 3.12
- Docker
- PX4 SITL + Gazebo
- MAVSDK
- at least several GB of free disk space

For a fresh evaluation machine, 16 GB RAM and at least 15–20 GB free disk space are
recommended so that the PX4/Gazebo image, Python environment, evidence, and long soak
results have sufficient headroom.

Gazebo GUI is optional. Headless mode is recommended for reproducibility and long runs.

---

## 3. Required host tools

Check first:

```bash
git --version
python3 --version
docker --version
gh --version
```

If Git/Python/Docker are already working, do not reinstall them unnecessarily.

On Ubuntu, a minimal host bootstrap can be:

```bash
sudo apt update
sudo apt install -y \
  git \
  curl \
  python3 \
  python3-venv \
  python3-pip \
  unzip
```

Docker must be installed and usable by the current user.

Verify:

```bash
docker ps
```

If Docker requires `sudo`, configure the current user for Docker according to the
organization's normal host policy, then log out/in before continuing.

---

## 4. Authenticate to the private GitHub repository

If the repository is private:

```bash
gh auth login \
  --hostname github.com \
  --git-protocol https \
  --web
```

Then:

```bash
gh auth setup-git
```

Verify access:

```bash
gh repo view wisdombrainscience/auv-aigent
```

---

## 5. Clone the evaluation branch

Recommended fresh location:

```bash
mkdir -p ~/UAV
cd ~/UAV

gh repo clone \
  wisdombrainscience/auv-aigent \
  remote_UAV_evaluation_leader
```

Enter the repository:

```bash
cd ~/UAV/remote_UAV_evaluation_leader
```

Fetch branches:

```bash
git fetch origin
```

Checkout the evaluation branch:

```bash
git switch \
  --track origin/evaluation/v1.6.1-verification
```

If the local tracking branch already exists:

```bash
git switch evaluation/v1.6.1-verification
git pull --ff-only
```

Verify:

```bash
git branch --show-current
git log -1 --oneline --decorate
git status
```

Expected branch:

```text
evaluation/v1.6.1-verification
```

The working tree should be clean before installation/testing.

---

## 6. Optional: pin the exact review commit

For an exact review snapshot instead of the moving branch head:

```bash
git checkout 93a3033
```

This produces a detached HEAD intentionally.

To return to the branch later:

```bash
git switch evaluation/v1.6.1-verification
```

For long-term reproducibility, a signed/annotated Git tag is preferable to a moving
branch.

---

## 7. Install the project environment

From the repository root:

```bash
cd ~/UAV/remote_UAV_evaluation_leader
```

Ensure the command facade is executable:

```bash
chmod +x uavctl evaluation-uav start-uav
```

Install the development environment and simulator dependencies:

```bash
./uavctl install --dev --pull-sim
```

If the simulator image is already installed and network bandwidth should be avoided:

```bash
./uavctl install --dev
```

Do not manually substitute a different PX4/Gazebo image when trying to reproduce the
recorded evaluation.

---

## 8. Create a fresh local runtime configuration

`uav.env` is local runtime configuration and should not be committed.

Create a leader-specific data root:

```bash
mkdir -p ~/UAV/remote_uav_eval_leader_data/runs
```

Create `uav.env`:

```bash
cat > uav.env <<'EOF'
UAV_DATA_DIR="$HOME/UAV/remote_uav_eval_leader_data"
UAV_SIM_BACKEND="gazebo"
UAV_PORT="8470"
UAV_SIM_HOST_LOG_MODE="docker"
EOF
```

Important:

```text
UAV_SIM_HOST_LOG_MODE="docker"
```

prevents the high-volume PX4/Gazebo stdout stream from being mirrored into an unbounded
host `simulator.log`.

Do not commit `uav.env`.

---

## 9. Preflight

Run:

```bash
./uavctl doctor
```

Also verify:

```bash
docker ps
df -h /
```

The evaluation should begin with:

- enough free disk space
- no stale Remote UAV simulator container
- no unrelated heavy CPU/RAM/I/O workload
- a clean evaluation Git branch
- a writable `UAV_DATA_DIR`

Inspect the configured PX4 image if needed:

```bash
docker images | grep 'px4io/px4-sitl-gazebo'
```

If the required simulator image is missing, rerun:

```bash
./uavctl install --pull-sim
```

---

## 10. Verify the committed evaluation files

The branch contains the frozen key-file manifest:

```bash
sha256sum -c \
  docs/evaluation/baseline_v1_6_1/key_files.sha256
```

Expected:

```text
evaluation/direct_runtime.py: OK
evaluation/run_comparative.py: OK
evaluation/run_reliability.py: OK
evaluation/run_longitudinal.py: OK
evaluation-uav: OK
orchestration/step37a_simulation.py: OK
```

The official Comparative evidence also has its own checksum:

```bash
(
  cd docs/evaluation/results
  sha256sum -c SHA256SUMS.txt
)
```

Expected:

```text
comparative_primary_30cycles.txt: OK
```

---

## 11. Run regression tests before flight evaluation

Targeted evaluation/simulation tests:

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
.venv/bin/python -m pytest -q \
  tests/unit/test_evaluation_bounded_simulator_log.py \
  tests/unit/test_evaluation_startup_readiness.py \
  tests/unit/test_evaluation_reliability_readiness.py \
  tests/unit/test_evaluation_reliability_v16.py \
  tests/unit/test_step37a_simulation_backend.py \
  tests/unit/test_step37a_simulation_readiness.py \
  tests/unit/test_px4_stream_cleanup.py
```

Recorded isolated-branch result:

```text
21 passed
```

Full branch regression:

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
.venv/bin/python -m pytest -q
```

Recorded isolated review-branch result:

```text
2834 passed
```

The historical frozen experimental working copy recorded `3028 passed` because it
included the then-current local Phase 13 source scope. The review branch intentionally
does not import that uncommitted Phase 13 workspace. See:

```text
docs/evaluation/BRANCH_REPRODUCIBILITY_NOTE.md
```

---

## 12. First smoke test — Comparative

Before a 30-cycle run, execute a short smoke:

```bash
set -o pipefail

UAV_GAZEBO_GUI=off \
./evaluation-uav comparative --cycles 2 \
  2>&1 | tee /tmp/remote_uav_comparative_smoke.log
```

This uses fresh PX4 SITL/Gazebo mission cycles.

A successful smoke should produce a report path under:

```text
reports/evaluation/comparative_<timestamp>/
```

Do not delete a failure merely because the smoke is unsuccessful. Inspect the reported
phase/reason first.

---

## 13. Reproduce the Official Comparative protocol

Create a local log directory:

```bash
mkdir -p ~/UAV/REMOTE_UAV_EVAL_LEADER_LOGS
```

Run:

```bash
set -o pipefail

UAV_GAZEBO_GUI=off \
./evaluation-uav comparative --cycles 30 \
  2>&1 | tee \
  ~/UAV/REMOTE_UAV_EVAL_LEADER_LOGS/comparative_30cycles.log
```

The original primary campaign recorded:

```text
requested cycles                  30
cycles reaching flight readiness 25
infrastructure exclusions         5
valid capability executions      100
AGREE_PASS                        100
AGREE_FAIL                        0
SILENT_FAILURE                    0
VERIFIER_ONLY_LENIENT             0
```

A new machine is a replication run. It is not expected to reproduce the exact same
startup-exclusion count deterministically.

Do not run replacement successful cycles and silently merge them into the primary
campaign.

---

## 14. Reliability / fault-injection campaign

Reliability uses the localhost Web API for the CONTINUE-chained condition.

### Terminal 1 — start the local console

From the repository root:

```bash
UAV_GAZEBO_GUI=off \
UAV_PORT=8470 \
./start-uav
```

Keep this terminal running.

### Terminal 2 — smoke reliability

```bash
cd ~/UAV/remote_UAV_evaluation_leader

./evaluation-uav reliability \
  --trials 2 \
  --fault-trials 2 \
  --continue-flights 4 \
  --max-infra-exclusions-per-condition 2 \
  --base-url http://127.0.0.1:8470
```

Expected smoke semantics:

- Fresh near: verifier PASS
- Fresh far: verifier PASS
- CONTINUE-chained: verifier PASS
- injected telemetry dropout: verifier FAIL as expected
- injected timeout: module FAIL + verifier FAIL as expected

For fault conditions, verifier FAIL can be the correct expected behavior.

### Report-quality reliability campaign

The current declared protocol is:

```bash
set -o pipefail

./evaluation-uav reliability \
  --trials 30 \
  --fault-trials 30 \
  --continue-flights 30 \
  --max-infra-exclusions-per-condition 6 \
  --base-url http://127.0.0.1:8470 \
  2>&1 | tee \
  ~/UAV/REMOTE_UAV_EVAL_LEADER_LOGS/reliability.log
```

Do not increase the infrastructure-exclusion budget after the campaign starts.

After the campaign, stop Terminal 1 with `Ctrl+C`.

---

## 15. Longitudinal soak

The longitudinal harness can auto-start a localhost console when no console is
listening, unless `--no-auto-start-console` is supplied.

Always run a short smoke first:

```bash
set -o pipefail

UAV_GAZEBO_GUI=off \
./evaluation-uav longitudinal \
  --hours 0.05 \
  --base-url http://127.0.0.1:8470 \
  2>&1 | tee \
  ~/UAV/REMOTE_UAV_EVAL_LEADER_LOGS/longitudinal_smoke.log
```

If the smoke succeeds, run the >=4 h campaign:

```bash
set -o pipefail

UAV_GAZEBO_GUI=off \
./evaluation-uav longitudinal \
  --hours 4 \
  --base-url http://127.0.0.1:8470 \
  2>&1 | tee \
  ~/UAV/REMOTE_UAV_EVAL_LEADER_LOGS/longitudinal_4h.log
```

Do not suspend the machine. Avoid unrelated high-load jobs.

The leader-facing checkpoints are:

```text
0 min
60 min
120 min
240 min
```

Metrics include:

- RSS
- open file descriptors
- residual MAVSDK process count
- evidence/storage usage
- simulator host-log size
- p95 API latency
- completed/PASS flights

---

## 16. Aggregate leader-ready tables

After Comparative, Reliability, and Longitudinal result directories exist:

```bash
./evaluation-uav aggregate
```

The output is written under:

```text
reports/evaluation/leader_tables_<timestamp>/
```

The Markdown leader table is:

```text
LEADER_EVALUATION_TABLES.md
```

Keep the raw report directories together with the aggregated table.

---

## 17. Important interpretation rules

### Capability failure vs infrastructure failure

A PX4 startup/readiness failure that happens before a capability execution window opens
is an infrastructure exclusion, not a TAKEOFF/NAV/RTH/LAND capability failure.

It must still be reported.

### Module `completed` is not verdict authority

Examples:

```text
module completed + verifier PASS = AGREE_PASS
module failed    + verifier FAIL = AGREE_FAIL
module completed + verifier FAIL = SILENT_FAILURE
module failed    + verifier PASS = VERIFIER_ONLY_LENIENT
```

### Fault-injection PASS semantics

For telemetry-dropout and timeout conditions, raw verifier FAIL may be expected.
Use the expected-behavior metric rather than interpreting all verifier FAIL values as a
failed fault-injection campaign.

---

## 18. Keep replication data separate

Do not write a leader replication into the historical campaign directories.

Use a fresh `UAV_DATA_DIR` and fresh `reports/evaluation` output in the leader clone.

Do not copy historical `runs/` into the leader machine before running a replication.

Do not delete failed attempts.

---

## 19. Troubleshooting

### `Repository not found`

Authenticate the GitHub CLI and Git credential helper:

```bash
gh auth login --hostname github.com --git-protocol https --web
gh auth setup-git
```

Then:

```bash
git ls-remote origin HEAD
```

### Docker permission denied

Verify:

```bash
docker ps
```

Fix Docker-user permissions according to the organization's host policy and re-login.

### Simulator image missing

Run:

```bash
./uavctl install --pull-sim
```

### `COMMAND_DENIED` / PX4 is not armable

Do not convert this into a capability PASS/FAIL manually.

The evaluation readiness gate should classify bounded pre-execution readiness failures
as infrastructure exclusions.

### Host disk grows rapidly

Check:

```bash
du -sh "$UAV_DATA_DIR" 2>/dev/null || true
df -h /
```

Confirm:

```bash
grep UAV_SIM_HOST_LOG_MODE uav.env
```

Expected:

```text
UAV_SIM_HOST_LOG_MODE="docker"
```

### Port 8470 already in use

Check:

```bash
ss -ltnp | grep ':8470'
```

Stop the stale Remote UAV console before beginning a fresh campaign.

---

## 20. Minimal leader workflow

For a reviewer who only wants to validate that the branch works:

```bash
gh repo clone wisdombrainscience/auv-aigent ~/UAV/remote_UAV_evaluation_leader
cd ~/UAV/remote_UAV_evaluation_leader
git switch --track origin/evaluation/v1.6.1-verification

./uavctl install --dev --pull-sim

mkdir -p ~/UAV/remote_uav_eval_leader_data/runs

cat > uav.env <<'EOF'
UAV_DATA_DIR="$HOME/UAV/remote_uav_eval_leader_data"
UAV_SIM_BACKEND="gazebo"
UAV_PORT="8470"
UAV_SIM_HOST_LOG_MODE="docker"
EOF

./uavctl doctor

sha256sum -c docs/evaluation/baseline_v1_6_1/key_files.sha256

PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
.venv/bin/python -m pytest -q

UAV_GAZEBO_GUI=off \
./evaluation-uav comparative --cycles 2
```

If these steps succeed, the branch is installed and the evaluation harness is operational.

---

## 21. Files to read

Start with:

```text
docs/evaluation/REMOTE_UAV_EVALUATION_V1_6_1_PROTOCOL.md
docs/evaluation/BRANCH_REPRODUCIBILITY_NOTE.md
docs/evaluation/RESULTS_CURRENT.md
evaluation/README.md
```

Official primary Comparative evidence:

```text
docs/evaluation/results/comparative_primary_30cycles.txt
docs/evaluation/results/SHA256SUMS.txt
```

Frozen source/environment metadata:

```text
docs/evaluation/baseline_v1_6_1/
```
