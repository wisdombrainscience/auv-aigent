# S3.3 — Create/Open Project

Status: candidate for target gate.

S3.3 connects the S3.1 SQLite schema with the S3.2 filesystem layout. SQLite is the authority for project existence and persistent project-ID allocation. The filesystem stores the fixed project directory tree and `project.json`.

Storage root:

```text
~/.local/share/remote-uav/
├── project_state.sqlite3
└── projects/
    └── UAV-PROJ-000001/
        ├── project.json
        └── <14 fixed S3.2 directories>
```

`create_project()` allocates only the next `project` canonical ID using the S3.1 `canonical_id_sequences` row, inserts the project row, creates the fixed layout, writes deterministic metadata, and commits. A normal creation failure rolls back the SQLite allocation and removes the new project directory.

`open_project()` is read-only. It requires the project to exist in SQLite, validates the complete S3.2 directory layout, strictly validates `project.json`, and returns an immutable `ProjectHandle`.

`project.json` contains exactly:

```json
{
  "format_version": 1,
  "project_id": "UAV-PROJ-000001",
  "project_schema_version": 1,
  "project_layout_contract_version": "1.0",
  "project_store_contract_version": "1.0"
}
```

No timestamps are stored at S3.3 so metadata is deterministic. Duplicate JSON keys, unknown keys, wrong versions, symlinks, missing database rows, missing directories, and oversized metadata fail closed.

S3.3 deliberately does not add RequirementSet data, facts, assumptions, decisions, a reusable transaction API, artifact state, task state, Agent Engine, Orchestrator, RAG, or UAV execution changes. The reusable transaction wrapper remains S3.9.
