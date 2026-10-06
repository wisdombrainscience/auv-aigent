# Gate — Phase 3 S3.2 Filesystem Project Layout

S3.2 is GREEN only when:

- S3.1 is GREEN.
- installer preflight/apply pass.
- full target regression has zero failures.
- the frozen 14-directory project layout is exact.
- canonical project IDs are required for project directory names.
- layout creation is idempotent.
- restart/reconstruction validates the same paths.
- missing/wrong-type paths fail closed.
- project and child symlinks fail closed.
- `project.json` is reserved but not created early.
- S3.2 does not allocate IDs or write SQLite project state.
- live acceptance returns:
  `PROJECT_LAYOUT=PASS dirs=14 project_json=reserved restart=PASS`.

No SITL/Gazebo acceptance is required because S3.2 does not alter the UAV
execution plane.
