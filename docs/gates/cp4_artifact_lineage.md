# CP4 — Artifact Lineage Stable

CP4 is GREEN only after A4.1 through A4.8 are GREEN on the target.

Required final target evidence:

```text
./uavctl test
python3 tools/check_artifact_graph.py
```

The graph acceptance must prove version-specific lineage, deterministic
invalidation, currentness/supersession behavior, restart persistence, integrity
failure behavior, and unchanged Phase-3 Project State schema.

After CP4, the frozen roadmap proceeds to Phase 5 — Task Engine, beginning with
T5.1 task persistence.
