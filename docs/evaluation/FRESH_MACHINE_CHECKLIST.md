# Fresh Machine Checklist

- [ ] Ubuntu/Linux x86_64 host prepared
- [ ] Git, Python 3, venv, Docker and GitHub CLI installed
- [ ] `docker ps` works without sudo
- [ ] GitHub account can access `wisdombrainscience/auv-aigent`
- [ ] Repository cloned
- [ ] Branch `evaluation/v1.6.1-verification` checked out
- [ ] Working tree clean before setup
- [ ] `./tools/bootstrap_evaluation_machine.sh` completed
- [ ] machine-specific `UAV_DATA_DIR` configured
- [ ] `UAV_SIM_HOST_LOG_MODE="docker"`
- [ ] `./uavctl doctor` healthy
- [ ] frozen key SHA256 checks pass
- [ ] committed Comparative evidence SHA256 passes
- [ ] targeted regression passes
- [ ] full branch regression passes
- [ ] 2-cycle Comparative smoke completed
- [ ] enough free disk before 30-cycle / 4 h campaigns
- [ ] failures preserved
- [ ] replication data kept separate from the historical primary campaign

- [ ] 10-hour continuous longitudinal campaign uses `./tools/run_longitudinal_campaign.sh --hours 10`
- [ ] 4 h / 8 h / 10 h checkpoints belong to the same continuous run unless independent replications are explicitly requested
- [ ] early termination is retained as incomplete evidence and is not patched by appending another run
