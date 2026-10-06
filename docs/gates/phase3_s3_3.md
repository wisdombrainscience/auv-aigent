# Gate — Phase 3 S3.3 Create/Open Project

S3.3 is GREEN only when:

- S3.2 is GREEN;
- installer preflight/apply pass;
- full target regression has zero failures;
- first project allocates `UAV-PROJ-000001`;
- a restart can open the same project without mutation;
- the next creation allocates `UAV-PROJ-000002`;
- SQLite project row and `project` sequence remain consistent;
- exactly the S3.2 fixed layout is created;
- `project.json` is strict, deterministic, bounded, and fail-closed;
- symlink and partial-state conflicts are rejected;
- ordinary create failure rolls back DB allocation and new filesystem state;
- no general transaction API is introduced before S3.9;
- live acceptance returns `PROJECT_CREATE_OPEN=PASS first=UAV-PROJ-000001 second=UAV-PROJ-000002 restart=PASS db=project_state.sqlite3`.

No SITL/Gazebo acceptance is required because S3.3 does not alter the UAV execution plane.
