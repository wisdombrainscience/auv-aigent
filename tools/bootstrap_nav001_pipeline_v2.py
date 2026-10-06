#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import py_compile
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_PROJECT = Path.home() / "UAV" / "st0_project"
DEFAULT_CANDIDATE = (
    Path.home()
    / "UAV"
    / "st0_agent_jobs"
    / "NAV-001"
    / "recovery_002"
    / "workspace"
    / "src"
    / "capabilities"
    / "navigation"
    / "go_to_target.py"
)

REQUIRED = [
    "specs/NAV-001.yaml",
    "src/core/base_capability.py",
    "src/core/result.py",
    "src/core/errors.py",
    "src/adapters/base_uav_adapter.py",
    "src/verification/goto_target_verifier.py",
    "tests/unit/test_manual_goto_target.py",
]

OPTIONAL = [
    "config/boundaries.yaml",
    "user_interface/input_schema.py",
    "orchestration/runtime_input_bridge.py",
]


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def safe_copy(src: Path, snapshot_root: Path, rel: str) -> None:
    dst = snapshot_root / rel
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)


def run_cmd(cmd: list[str], cwd: Path) -> dict:
    p = subprocess.run(
        cmd,
        cwd=str(cwd),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
        env={
            **os.environ,
            "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
            "PYTHONPATH": str(cwd),
        },
    )
    return {
        "command": cmd,
        "exit_code": p.returncode,
        "output": p.stdout,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", type=Path, default=DEFAULT_PROJECT)
    parser.add_argument("--candidate", type=Path, default=DEFAULT_CANDIDATE)
    parser.add_argument("--run-targeted-tests", action="store_true")
    args = parser.parse_args()

    project = args.project.expanduser().resolve()
    candidate = args.candidate.expanduser().resolve()

    if not project.is_dir():
        print(f"PROJECT: FAIL: {project}")
        return 2

    report = project / "reports" / "pipeline_v2" / "bootstrap_audit"
    snapshot = report / "snapshot"

    if report.exists():
        shutil.rmtree(report)
    snapshot.mkdir(parents=True, exist_ok=True)

    files = {}
    missing_required = []

    for rel in REQUIRED + OPTIONAL:
        src = project / rel
        required = rel in REQUIRED
        if src.is_file():
            safe_copy(src, snapshot, rel)
            files[rel] = {
                "required": required,
                "exists": True,
                "sha256": sha256(src),
                "bytes": src.stat().st_size,
            }
        else:
            files[rel] = {"required": required, "exists": False}
            if required:
                missing_required.append(rel)

    candidate_info = {"path": str(candidate), "exists": candidate.is_file()}
    if candidate.is_file():
        candidate_dst = snapshot / "candidate" / "go_to_target.py"
        candidate_dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(candidate, candidate_dst)
        candidate_info["sha256"] = sha256(candidate)
        candidate_info["bytes"] = candidate.stat().st_size
        try:
            py_compile.compile(str(candidate), doraise=True)
            candidate_info["py_compile"] = "PASS"
        except Exception as exc:
            candidate_info["py_compile"] = "FAIL"
            candidate_info["py_compile_error"] = repr(exc)

    protected = [
        "specs/NAV-001.yaml",
        "src/core/base_capability.py",
        "src/core/result.py",
        "src/core/errors.py",
        "src/adapters/base_uav_adapter.py",
        "src/verification/goto_target_verifier.py",
    ]

    protected_lines = []
    for rel in protected:
        p = project / rel
        if p.is_file():
            protected_lines.append(f"{sha256(p)}  {rel}")

    (report / "protected_baseline.sha256").write_text(
        "\n".join(protected_lines) + ("\n" if protected_lines else ""),
        encoding="utf-8",
    )

    audit = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "project": str(project),
        "candidate": candidate_info,
        "files": files,
        "missing_required": missing_required,
        "status": "PASS" if not missing_required else "FAIL",
    }

    (report / "audit.json").write_text(
        json.dumps(audit, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    context_parts = [
        "# NAV-001 PIPELINE V2 CONTRACT CONTEXT",
        f"# project: {project}",
        f"# candidate: {candidate}",
        "",
    ]

    for rel in REQUIRED:
        p = project / rel
        context_parts.append("=" * 80)
        context_parts.append(rel)
        context_parts.append("=" * 80)
        if p.is_file():
            context_parts.append(p.read_text(encoding="utf-8", errors="replace"))
        else:
            context_parts.append("<MISSING>")
        context_parts.append("")

    if candidate.is_file():
        context_parts += [
            "=" * 80,
            "CURRENT AI CANDIDATE",
            "=" * 80,
            candidate.read_text(encoding="utf-8", errors="replace"),
            "",
        ]

    (report / "contract_context.txt").write_text(
        "\n".join(context_parts),
        encoding="utf-8",
    )

    if args.run_targeted_tests:
        venv_python = project / ".venv" / "bin" / "python"
        python_cmd = str(venv_python) if venv_python.is_file() else "python"
        result = run_cmd(
            [python_cmd, "-m", "pytest", "-q", "tests/unit/test_manual_goto_target.py"],
            cwd=project,
        )
        (report / "targeted_tests.json").write_text(
            json.dumps(result, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        (report / "targeted_tests.log").write_text(result["output"], encoding="utf-8")

    print("=== NAV-001 PIPELINE V2 BOOTSTRAP AUDIT ===")
    print(f"PROJECT={project}")
    print(f"REPORT={report}")
    print(f"REQUIRED_FILES={'PASS' if not missing_required else 'FAIL'}")
    for rel in missing_required:
        print(f"MISSING={rel}")
    print("CANDIDATE=" + ("FOUND" if candidate.is_file() else "NOT_FOUND"))
    if candidate.is_file():
        print(f"CANDIDATE_PY_COMPILE={candidate_info.get('py_compile')}")
        print(f"CANDIDATE_SHA256={candidate_info.get('sha256')}")
    print(f"AUDIT_STATUS={audit['status']}")
    print("NEXT=Use contract_context.txt to freeze NAV-001 implementation contract.")
    return 0 if not missing_required else 1


if __name__ == "__main__":
    raise SystemExit(main())
