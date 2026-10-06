# X10.2 — Role Allowlists

X10.2 adds an immutable, deterministic, deny-by-default mapping from the four frozen MVP roles to the logical tool identifiers registered by X10.1.

The mapping is taken conservatively from frozen architecture section 22:

- `coordinator`: no tool grant (the baseline gives no explicit coordinator example);
- `requirements_engineer`: `project_read`, `artifact_read`, `draft_write`;
- `uav_systems_engineer`: `project_read`, `calculator`, `artifact_read`, `draft_write`;
- `reviewer`: `artifact_read`, `project_read`, `issue_create`.

`software_engineer` remains a reserved specialized role and is not activated by X10.2. Workspace and test-runner tools remain registered logical capabilities but are not granted to an active role.

The allowlist registry validates role IDs through the frozen RoleRegistry and tool IDs through the X10.1 ToolRegistry. Unknown roles, unknown tools, and known-but-ungranted pairs fail closed.

X10.2 does not add tool executors, subprocess/shell execution, timeout wrappers, filesystem path policy, structured tool results, audit logging, provider calls, PX4 access, raw MAVSDK access, or arbitrary network access. Those remain outside this gate.
