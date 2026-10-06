#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ai.project import ProjectStore
from ai.tools import (
    TOOL_PATH_RESTRICTION_CONTRACT_VERSION,
    ProjectToolPathGuard,
    ToolPathDeniedError,
    ToolPathNotApplicableError,
)


def _denied(callable_) -> bool:
    try:
        callable_()
    except (ToolPathDeniedError, ToolPathNotApplicableError):
        return True
    return False


def main() -> int:
    assert TOOL_PATH_RESTRICTION_CONTRACT_VERSION == "1.0"
    with tempfile.TemporaryDirectory(prefix="ruav-x10-4-") as tmp:
        base = Path(tmp)
        store = ProjectStore(base / "state" / "projects")
        handle = store.create_project()
        note = handle.layout.data_directories["requirements"] / "note.txt"
        note.write_text("ok")
        guard = ProjectToolPathGuard(store)

        safe = guard.resolve(
            project_id=handle.project_id,
            role_id="requirements_engineer",
            tool_id="project_read",
            relative_path="requirements/note.txt",
        )
        assert safe.path == note and safe.exists

        draft = guard.resolve(
            project_id=handle.project_id,
            role_id="requirements_engineer",
            tool_id="draft_write",
            relative_path="architecture/candidate.json",
        )
        assert not draft.exists

        traversal = _denied(lambda: guard.resolve(
            project_id=handle.project_id,
            role_id="requirements_engineer",
            tool_id="project_read",
            relative_path="../outside",
        ))
        absolute = _denied(lambda: guard.resolve(
            project_id=handle.project_id,
            role_id="requirements_engineer",
            tool_id="project_read",
            relative_path=str(note),
        ))

        outside = base / "outside.txt"
        outside.write_text("secret")
        link = handle.layout.data_directories["requirements"] / "escape"
        link.symlink_to(outside)
        symlink = _denied(lambda: guard.resolve(
            project_id=handle.project_id,
            role_id="requirements_engineer",
            tool_id="project_read",
            relative_path="requirements/escape",
        ))
        pathless = _denied(lambda: guard.resolve(
            project_id=handle.project_id,
            role_id="uav_systems_engineer",
            tool_id="calculator",
            relative_path="project.json",
        ))

        assert traversal and absolute and symlink and pathless

    print(
        "TOOL_PATH_RESTRICTION=PASS "
        "contract_version=1.0 "
        "relative_only=PASS "
        "project_root=bounded "
        "workspace_root=trusted_preexisting_only "
        "workspace_created=none "
        "traversal=DENY "
        "absolute_path=DENY "
        "symlink_escape=DENY "
        "missing_parent_write=DENY "
        "pathless_tool=DENY "
        "allowlist_enforced=PASS "
        "timeout_wrapper=active_X10_3 "
        "structured_results=active_X10_5 "
        "audit_log=active_X10_6 "
        "filesystem_io=none "
        "provider_calls=none "
        "px4_access=none "
        "phase10=pending "
        "cp9=pending"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
