#!/usr/bin/env python3
from __future__ import annotations
import argparse, subprocess
from pathlib import Path

DEFAULT_PROJECT = Path.home() / "UAV" / "st0_project"
DEFAULT_ROOT = Path.home() / "UAV" / "st0_agent_jobs" / "NAV-001" / "step36_benchmark_v3"

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("model_id")
    ap.add_argument("--project", type=Path, default=DEFAULT_PROJECT)
    ap.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    ap.add_argument("--full-tests", action="store_true")
    args = ap.parse_args()

    project = args.project.expanduser().resolve()
    root = args.root.expanduser().resolve()
    run = root / "models" / args.model_id / "run_001_scored"
    candidate = run / "candidate/go_to_target.py"
    if not candidate.is_file():
        raise SystemExit("FAIL: candidate missing")

    report = run / ("candidate_validation_full.json" if args.full_tests else "candidate_validation_targeted.json")
    if report.exists():
        raise SystemExit("REFUSE: validation report already exists")

    cmd = [
        str(project / ".venv/bin/python"),
        str(project / "tools/validate_candidate.py"),
        "--candidate", str(candidate),
        "--report", str(report),
        "--full-tests" if args.full_tests else "--targeted-tests",
    ]
    return subprocess.run(cmd, cwd=project, check=False).returncode

if __name__ == "__main__":
    raise SystemExit(main())
