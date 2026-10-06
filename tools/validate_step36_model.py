#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

DEFAULT_PROJECT = Path.home() / "UAV" / "st0_project"
DEFAULT_ROOT = Path.home() / "UAV" / "st0_agent_jobs" / "NAV-001" / "step36_benchmark"


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("model_id")
    ap.add_argument("--project", type=Path, default=DEFAULT_PROJECT)
    ap.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    ap.add_argument("--full-tests", action="store_true")
    args = ap.parse_args()

    project = args.project.expanduser().resolve()
    root = args.root.expanduser().resolve()
    model_dir = root / "models" / args.model_id / "run_001"
    candidate = model_dir / "candidate/go_to_target.py"

    if not candidate.is_file():
        raise SystemExit("FAIL: candidate does not exist")

    report = model_dir / (
        "candidate_validation_full.json"
        if args.full_tests
        else "candidate_validation_targeted.json"
    )

    cmd = [
        str(project / ".venv/bin/python"),
        str(project / "tools/validate_candidate.py"),
        "--candidate", str(candidate),
        "--report", str(report),
    ]
    if args.full_tests:
        cmd.append("--full-tests")
    else:
        cmd.append("--targeted-tests")

    p = subprocess.run(cmd, cwd=project, text=True, check=False)
    return p.returncode


if __name__ == "__main__":
    raise SystemExit(main())
