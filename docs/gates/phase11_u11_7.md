# Gate U11.7 — UAV Run → Project Linkage

## Parent

- U11.6 GREEN.
- U11.3-H1 Scenario Path Hardening VERIFIED.
- Phase 10 GREEN.
- CP9 remains PENDING until this gate is proven on the target.
- Frozen baseline: `RUAV-AI-ARCH-V8.0-BL1`.

## Acceptance

U11.7 passes when all of the following hold:

1. exact Project State requirement/source-artifact lineage must exist;
2. only finalized, integrity-valid existing Remote UAV run evidence is linked;
3. proposal payload, runtime result and verifier evidence are cross-bound;
4. run links persist project-locally under `simulation/`;
5. persistence is idempotent and restart-safe;
6. conflicting reuse of a runtime run ID fails closed;
7. PASS and FAIL verifier results are both linkable without bridge
   reinterpretation;
8. `SIMULATION_RESULT` and `VERIFIER_RESULT` remain distinct provenance;
9. Project State core schema remains exactly three tables;
10. no provider, PX4, raw MAVSDK, retry, issue routing or Phase-12 feedback is
    added.

Target acceptance command:

```bash
python3 tools/check_phase11_uav_run_project_linkage.py
```

Expected leading marker:

```text
UAV_RUN_PROJECT_LINKAGE=PASS
```

The probe reports `phase11=READY cp9=READY`. Those are readiness markers only;
GREEN status is granted only after the target installer, full regression, and
acceptance evidence are all posted.
