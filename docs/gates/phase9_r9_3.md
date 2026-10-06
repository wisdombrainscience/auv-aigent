# Gate R9.3 — Review Task Generation

Candidate acceptance requires:

- exact-parent R9.2 GREEN / CP8 GREEN;
- one persisted `reviewer` task for a DRAFT artifact produced by a task with `review_required=true`;
- exact artifact-version input binding and producer-task dependency;
- `review_result.v1` output-schema metadata;
- `CREATED`, `attempt=0`, `review_required=false`;
- same-process and restart idempotency;
- no artifact lifecycle mutation, issue persistence/routing, rework, provider call, or task execution;
- full regression green.

R9.4–R9.7 remain explicitly deferred. CP9 remains pending.
