# S3.2 — Filesystem Project Layout

Status: candidate for target gate.

S3.2 freezes the filesystem side of the Project State storage architecture from
the V8 baseline.

Default local root:

```text
~/.local/share/remote-uav/projects/
```

One project layout:

```text
UAV-PROJ-000001/
├── project.json              # reserved path; S3.3 owns creation
├── requirements/
├── architecture/
├── propulsion/
├── power/
├── sensors/
├── px4/
├── missions/
├── software/
├── simulation/
├── tests/
├── tasks/
├── ai_runs/
├── evidence/
└── reports/
```

## S3.2 responsibility

S3.2 provides deterministic path calculation, fixed directory creation, layout
validation, symlink rejection, and restart-safe reconstruction.

It does **not**:

- allocate project IDs;
- insert or update SQLite project rows;
- create `project.json`;
- implement create/open project semantics;
- create RequirementSet, facts, assumptions, or decisions;
- write artifacts or task state.

Those responsibilities remain gated to S3.3 and later.

`project.json` is intentionally only a reserved canonical path in S3.2 so S3.3
can coordinate project metadata with SQLite create/open semantics.

The directory set is fixed and bounded at 14 entries. S3.2 never accepts an
agent-provided arbitrary subdirectory name.
