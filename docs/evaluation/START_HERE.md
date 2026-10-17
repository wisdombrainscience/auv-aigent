# Remote UAV Evaluation v1.6.1 — START HERE

Use this directory to install and run the evaluation branch on a fresh machine.

Start with:

```text
docs/evaluation/LEADER_SETUP_AND_RUN.md
```

After cloning `evaluation/v1.6.1-verification`, the shortest supported path is:

```bash
./tools/bootstrap_evaluation_machine.sh
./tools/check_evaluation_ready.sh
./tools/run_evaluation_smoke.sh
```

Then follow the full runbook for Comparative, Reliability, Longitudinal and aggregation.

Important rules:

- Scope is `SIMULATION_ONLY`.
- Independent verifier is verdict authority.
- Do not delete failures.
- Do not change thresholds after seeing results.
- Do not silently replace failed samples with successful reruns.
- Do not merge the Draft PR into `main` merely to run the evaluation branch.
