# Remote UAV Evaluation v1.6.1 — Leader 4 h / 8 h / 10 h Execution Runbook

This runbook is the operator procedure for a leader/reviewer who wants to reproduce the
Remote UAV evaluation from a fresh machine and collect long-duration evidence at 4 h,
8 h, and 10 h.

It follows the same evaluation branch, readiness rules, evidence discipline, and
failure-preservation rules used by the successful v1.6.1 campaign.

## 1. Immutable scope

Repository:

```text
wisdombrainscience/auv-aigent
```

Evaluation branch:

```text
evaluation/v1.6.1-verification
```

Committed project base:

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

Do not merge the Draft evaluation PR into `main` simply to run the campaign.

---

## 2. Recommended experimental design

For the question "what happens after 4 h, 8 h, and 10 h of continuous operation?", use
one continuous 10-hour longitudinal campaign.

Mandatory interpretation checkpoints:

```text
T0   = start
T4   = 4 h  = 240 min
T8   = 8 h  = 480 min
T10  = 10 h = 600 min
```

The system must not be restarted between T0, T4, T8, and T10.

This is preferred over three separate runs because it measures one continuous process and
preserves cumulative resource behavior.

If the leader instead wants three independent replication campaigns, run 4 h, 8 h, and
10 h separately. These are three datasets and must not be merged as if they were one
continuous 22-hour run.

---

## 3. Fresh machine bootstrap

On Ubuntu:

```bash
sudo apt update
sudo apt install -y \
  git \
  curl \
  unzip \
  python3 \
  python3-venv \
  python3-pip \
  docker.io \
  gh
```

Enable Docker:

```bash
sudo systemctl enable --now docker
```

Verify:

```bash
docker ps
```

If permission is denied, configure the current user according to the local Docker policy.
A common development setup is:

```bash
sudo usermod -aG docker "$USER"
```

Log out/in after changing Docker group membership.

Authenticate to the private repository:

```bash
gh auth login \
  --hostname github.com \
  --git-protocol https \
  --web

gh auth setup-git
```

Verify:

```bash
gh repo view wisdombrainscience/auv-aigent
```

---

## 4. Clone the evaluation branch

```bash
mkdir -p ~/UAV
cd ~/UAV

gh repo clone \
  wisdombrainscience/auv-aigent \
  remote_UAV_evaluation_leader
```

Enter:

```bash
cd ~/UAV/remote_UAV_evaluation_leader
```

Switch to evaluation branch:

```bash
git fetch origin

git switch \
  --track origin/evaluation/v1.6.1-verification
```

Verify:

```bash
git branch --show-current
git status
git log -1 --oneline --decorate
```

Expected branch:

```text
evaluation/v1.6.1-verification
```

The working tree should be clean before installation.

---

## 5. Install exactly as the evaluation branch expects

Use the committed bootstrap helper:

```bash
./tools/bootstrap_evaluation_machine.sh
```

Default data root:

```text
$HOME/UAV/remote_uav_eval_leader_data
```

The bootstrap creates/uses local `uav.env` with:

```text
UAV_DATA_DIR="$HOME/UAV/remote_uav_eval_leader_data"
UAV_SIM_BACKEND="gazebo"
UAV_PORT="8470"
UAV_SIM_HOST_LOG_MODE="docker"
```

The `docker` host-log mode is required for long runs because an earlier development
configuration could mirror simulator stdout into a host file and grow it by multiple GB.

Do not commit `uav.env`.

---

## 6. Pre-campaign integrity checks

Run:

```bash
./tools/check_evaluation_ready.sh
```

Then verify the frozen key files:

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

Verify committed Comparative evidence:

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

Check disk:

```bash
df -h /
```

Recommended before a 10-hour run:

```text
at least 15–20 GB free
```

Check bounded logging:

```bash
grep UAV_SIM_HOST_LOG_MODE uav.env
```

Expected:

```text
UAV_SIM_HOST_LOG_MODE="docker"
```

---

## 7. Regression gate

Targeted evaluation regression:

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

Recorded isolated-branch reference:

```text
21 passed
```

Full branch regression:

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
.venv/bin/python -m pytest -q
```

Recorded isolated review-branch reference:

```text
2834 passed
```

The historical frozen experimental working copy recorded `3028 passed` under a different
source scope that included the then-current local Phase 13 workspace. See:

```text
docs/evaluation/BRANCH_REPRODUCIBILITY_NOTE.md
```

---

## 8. Smoke before any long run

Run a 2-cycle Comparative smoke:

```bash
./tools/run_evaluation_smoke.sh
```

Then run a short longitudinal smoke:

```bash
mkdir -p ~/UAV/REMOTE_UAV_EVAL_LEADER_LOGS

set -o pipefail

UAV_GAZEBO_GUI=off \
./evaluation-uav longitudinal \
  --hours 0.05 \
  --base-url http://127.0.0.1:8470 \
  2>&1 | tee \
  ~/UAV/REMOTE_UAV_EVAL_LEADER_LOGS/longitudinal_smoke.log
```

Do not start a 10-hour run until the smoke path reaches the expected runtime/report path.

A smoke failure must be retained and diagnosed rather than deleted.

---

## 9. Primary 10-hour campaign

Use the committed wrapper:

```bash
./tools/run_longitudinal_campaign.sh --hours 10
```

This wrapper:

- runs the existing `evaluation-uav longitudinal` implementation
- forces headless Gazebo
- preserves the complete console log
- records Git commit, branch, host, Docker, Python, start/end time
- records the generated longitudinal report path when detectable
- produces SHA256 for the wrapper log and metadata
- never changes evaluation thresholds or verifier semantics

The actual core command remains:

```bash
UAV_GAZEBO_GUI=off \
./evaluation-uav longitudinal \
  --hours 10 \
  --base-url http://127.0.0.1:8470
```

The wrapper only preserves execution metadata/evidence around the existing command.

### During the run

Do not:

- suspend/sleep the machine
- reboot
- restart PX4/Gazebo between checkpoints
- run heavy AI training/VM/render/large compilation jobs
- change `uav.env`
- change thresholds
- modify evaluation code
- delete failures

Checkpoints:

```text
4 h  -> campaign is still running
8 h  -> campaign is still running
10 h -> campaign completes
```

Do not stop the process at 4 h or 8 h when the goal is one continuous 10-hour campaign.

---

## 10. Metrics to report at 4 h / 8 h / 10 h

The longitudinal evidence/report should be used to populate:

| Checkpoint | RSS | FDs | Residual MAVSDK | Storage | Simulator log | p95 API latency | Completed flights | Failed flights |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| T0 | ... | ... | ... | ... | ... | ... | ... | ... |
| T4 = 240 min | ... | ... | ... | ... | ... | ... | ... | ... |
| T8 = 480 min | ... | ... | ... | ... | ... | ... | ... | ... |
| T10 = 600 min | ... | ... | ... | ... | ... | ... | ... | ... |

Recommended additional intermediate checkpoints when present:

```text
1 h
2 h
6 h
```

These help distinguish startup/warm-up behavior from continuous resource growth.

Interpret resource trends rather than only the final value.

Example of a plausible warm-up/plateau pattern:

```text
RSS: 62 -> 74 -> 76 -> 76 MB
FDs: 6 -> 6 -> 6 -> 6
MAVSDK residual: 0 -> 0 -> 0 -> 0
```

A continuously rising RSS/FD sequence requires investigation; do not label it stable merely
because the run reaches 10 h.

---

## 11. Independent 4 h / 8 h / 10 h campaigns

Only use this design when the leader explicitly wants independent replications.

4 h:

```bash
./tools/run_longitudinal_campaign.sh --hours 4
```

8 h:

```bash
./tools/run_longitudinal_campaign.sh --hours 8
```

10 h:

```bash
./tools/run_longitudinal_campaign.sh --hours 10
```

Total requested runtime is 22 hours plus setup/retries.

Each run receives its own timestamped evidence directory.

Do not concatenate the three runs and call them one continuous 22-hour campaign.

---

## 12. What to do if the 10-hour run stops early

Suppose a 10-hour campaign terminates at 7.2 h.

Report:

```text
requested duration = 10 h
actual duration    = 7.2 h
status             = incomplete
termination reason = <actual reason>
```

Do not run another 2.8 h and append it.

If a retry is required, label it separately:

```text
10 h replication #2
```

Preserve the incomplete first attempt.

---

## 13. Infrastructure readiness is not capability correctness

A PX4 startup/readiness failure before capability execution is an infrastructure
failure/exclusion.

It is not automatically a TAKEOFF/NAV/RTH/LAND failure.

However, it must still be reported and retained.

Capability/execution classifications remain:

```text
module completed + verifier PASS = AGREE_PASS
module failed    + verifier FAIL = AGREE_FAIL
module completed + verifier FAIL = SILENT_FAILURE
module failed    + verifier PASS = VERIFIER_ONLY_LENIENT
```

---

## 14. Evidence locations

Wrapper logs/metadata:

```text
~/UAV/REMOTE_UAV_EVAL_LEADER_LOGS/
```

Project evaluation reports:

```text
reports/evaluation/
```

Runtime evidence:

```text
$UAV_DATA_DIR
```

Do not copy historical developer `runs/` into a fresh leader replication before starting.

---

## 15. After the campaign

Aggregate:

```bash
./evaluation-uav aggregate
```

Leader-facing tables are written under a directory similar to:

```text
reports/evaluation/leader_tables_<timestamp>/
```

Keep:

- raw longitudinal report directory
- wrapper log
- wrapper metadata
- wrapper SHA256
- aggregated leader tables
- failed attempts, if any

Do not commit multi-GB runtime datasets directly to Git.

Commit compact summaries/manifests only after review.

---

## 16. Minimal leader command sequence

After clone and checkout:

```bash
./tools/bootstrap_evaluation_machine.sh
./tools/check_evaluation_ready.sh
```

Regression:

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
.venv/bin/python -m pytest -q
```

Smoke:

```bash
./tools/run_evaluation_smoke.sh
```

Longitudinal smoke:

```bash
UAV_GAZEBO_GUI=off \
./evaluation-uav longitudinal \
  --hours 0.05 \
  --base-url http://127.0.0.1:8470
```

Primary campaign:

```bash
./tools/run_longitudinal_campaign.sh --hours 10
```

Aggregate:

```bash
./evaluation-uav aggregate
```

This is the recommended path for a leader who wants a continuous 10-hour dataset with
4 h, 8 h, and 10 h interpretation checkpoints.
