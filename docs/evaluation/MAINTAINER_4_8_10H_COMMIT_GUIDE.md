# Maintainer Commit Guide — Leader 4/8/10 h Support

Apply this pack to the evaluation branch, review, commit, push, then create/update the
leader-ready tag only after a fresh-clone smoke.

Expected additions:

```text
docs/evaluation/LEADER_LONGITUDINAL_4_8_10H_RUNBOOK.md
docs/evaluation/LONGITUDINAL_4_8_10H_PROTOCOL.md
tools/run_longitudinal_campaign.sh
```

The apply script also appends links to existing entry-point documentation.

Recommended commit:

```bash
git add \
  docs/evaluation/LEADER_LONGITUDINAL_4_8_10H_RUNBOOK.md \
  docs/evaluation/LONGITUDINAL_4_8_10H_PROTOCOL.md \
  docs/evaluation/START_HERE.md \
  docs/evaluation/LEADER_SETUP_AND_RUN.md \
  docs/evaluation/FRESH_MACHINE_CHECKLIST.md \
  evaluation/README.md \
  tools/run_longitudinal_campaign.sh

git commit -m \
"docs: add leader 4h 8h 10h longitudinal campaign workflow"

git push
```

Do not commit runtime logs or multi-GB `runs/` data.
