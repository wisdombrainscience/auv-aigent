# U11.7 — Link UAV Run Back to Project

## Purpose

Complete the Phase-11 UAV Bridge by persistently binding one finalized existing
Remote UAV mission run to the exact Project State lineage that produced its
`SimulationProposal`.

U11.7 does **not** parse verifier failures into engineering issues, invalidate
lineage, create rework tasks, or retry simulation. Those behaviors remain
reserved for Phase 12.

## Contract

`UAV_RUN_LINK_CONTRACT_VERSION = "1.0"`

Public API:

```python
record = link_uav_run_to_project(
    project_store,
    deployment_root,
    proposal,
    mission_experiment_result,
)
```

The persistent registry is project-local:

```text
<project>/simulation/uav_run_links.sqlite3
```

It is a dedicated two-table registry and does not modify the three-table
Project State core schema.

## Required lineage

Before a runtime result can be linked:

1. the `project_id` must exist in Project State;
2. the referenced immutable requirement version must exist;
3. every exact source artifact version must exist in the same project;
4. the runtime result mission ID must match the proposal;
5. the attempt must be inside the existing Remote UAV `runs/` root;
6. the attempt path must match `<mission>/<scenario>/attempt_NNN`;
7. the attempt evidence must already be finalized and pass integrity checking.

U11.7 intentionally requires historical lineage to **exist**, not to remain
current. A completed simulation run remains historical evidence even if later
engineering changes make its source artifact stale.

## Evidence binding

The linker verifies that the finalized runtime evidence matches the exact
proposal/result:

- `metadata.json` binds runtime run ID, mission ID and scenario ID;
- `mission_input.json` must equal the proposal mission payload;
- `mission_steps.json` must equal the runtime result step records;
- `verification.json` must equal the runtime result verifier object;
- adapter cleanup evidence must agree with the runtime result;
- finalized evidence integrity must pass.

The stored link contains SHA-256 anchors for:

- the exact proposal;
- the runtime result;
- `finalized.json`;
- `mission_input.json`;
- `mission_steps.json`;
- `verification.json`.

The registry records provenance separately as:

```text
SIMULATION_RESULT
VERIFIER_RESULT
```

U11.7 does not copy the verifier `overall` verdict into a bridge-owned status
field. Both PASS and FAIL runs are linked. The independent mission verifier
remains the sole simulation verdict authority.

## Idempotency and conflict behavior

Re-linking the exact same runtime run with the exact same immutable binding is
idempotent.

Reusing the same runtime run ID with conflicting project/proposal/evidence
binding fails closed.

## Security boundaries

U11.7 adds no:

- provider/model call;
- PX4 adapter construction;
- raw MAVSDK access;
- capability or mission authorization;
- runtime retry;
- issue classification;
- rework task;
- verifier reinterpretation.

The hardened U11.3 safe-scenario-path check remains a required upstream
boundary before U11.6 runtime invocation.

## Phase completion

When U11.7 installer application, full regression, and
`check_phase11_uav_run_project_linkage.py` all pass on the target, Phase 11 is
ready to be marked GREEN and CP9 (`UAV Bridge stable`) is ready to be marked
GREEN.

Phase 12 then begins with `F12.1 — verifier-result parser`.
