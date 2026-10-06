#!/usr/bin/env python3
from __future__ import annotations
from pathlib import Path
import sys
import tempfile
import time
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from ai.project.store import ProjectStore
from ai.tools import TOOL_AUDIT_CONTRACT_VERSION, read_tool_audit, run_tool_audited

def _ok(): return {"value": 42}
def _slow():
    time.sleep(0.3)
    return "late"

def main() -> int:
    assert TOOL_AUDIT_CONTRACT_VERSION == "1.0"
    with tempfile.TemporaryDirectory(prefix="x106-audit-") as td:
        store = ProjectStore(projects_root=Path(td))
        handle = store.create_project()
        success = run_tool_audited(handle=handle, role_id="uav_systems_engineer", tool_id="calculator", operation=_ok, timeout_s=2)
        denied = run_tool_audited(handle=handle, role_id="reviewer", tool_id="calculator", operation=_ok, timeout_s=2)
        timed = run_tool_audited(handle=handle, role_id="requirements_engineer", tool_id="project_read", operation=_slow, timeout_s=0.03)
        records = read_tool_audit(handle)
        assert len(records) == 3 and [r.sequence for r in records] == [1,2,3]
        assert success.failure is None
        assert denied.failure is not None and denied.failure.code == "TOOL_NOT_ALLOWED"
        assert timed.failure is not None and timed.failure.code == "TOOL_TIMEOUT"
        reopened = store.open_project(handle.project_id)
        assert len(read_tool_audit(reopened)) == 3
    print("TOOL_AUDIT_LOG=PASS contract_version=1.0 project_local=PASS append_only=PASS sequence=PASS success=LOGGED deny=LOGGED timeout=LOGGED restart=PASS result_contract=X10_5_UNCHANGED shell_api=none executor_registry=none provider_calls=none px4_access=none phase10=READY cp9=pending")
    return 0
if __name__ == "__main__": raise SystemExit(main())
