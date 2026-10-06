# Gate — Phase 3 S3.10 Restart/Recovery

S3.10 is GREEN only when:

- S3.9 is GREEN.
- installer preflight/apply pass.
- full target regression has zero failures.
- complete Phase-3 Project State survives a fresh-process reconstruction.
- immutable requirement v1 remains readable after later versions and restart.
- an interrupted visible transaction leaves a bounded PREPARED journal.
- explicit restart recovery restores the exact pre-commit facts,
  assumptions, and decisions.
- recovery clears the journal after durable rollback.
- corrupt/foreign/symlinked/unknown-target journals fail closed.
- a pending journal blocks new transaction work until recovered.
- a successful transaction removes its journal.
- S3.1 SQLite tables remain unchanged.
- no arbitrary filesystem/SQL interface, retry loop, cloud provider, or UAV
  execution-plane behavior is introduced.
- acceptance returns:
  `PROJECT_RECOVERY=PASS restart=PASS interrupted_commit=ROLLED_BACK requirements=PASS facts=PASS assumptions=PASS decisions=PASS journal=cleared schema_tables=3`.

Passing this gate closes CP3 — Project Persistence Stable.
