#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import os
import subprocess
import sys
from pathlib import Path

ACCEPTED = "baa5040a483e559602171fd1d42a7a2a27267a5271d582485e89205d61c0c7c6"


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def pytest_python(project: Path) -> str:
    candidates = [project / ".venv" / "bin" / "python", Path(sys.executable)]
    for candidate in candidates:
        if not candidate.is_file():
            continue
        check = subprocess.run(
            [str(candidate), "-c", "import pytest"],
            cwd=project,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        if check.returncode == 0:
            return str(candidate)
    return sys.executable


def main() -> int:
    default_project = Path(__file__).resolve().parents[1]
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", type=Path, default=default_project)
    args = ap.parse_args()
    project = args.project.expanduser().resolve()

    src = project / "src/capabilities/navigation/go_to_target.py"
    accepted = project / f"reports/pipeline_v2/accepted/NAV-001/{ACCEPTED}/go_to_target.py"
    print("PROJECT", project)
    print("CURRENT_SOURCE_SHA256", sha256(src) if src.is_file() else "MISSING")
    print("HISTORICAL_ACCEPTED_SHA256", sha256(accepted) if accepted.is_file() else "MISSING")
    print(
        "SOURCE_MATCHES_HISTORICAL_ACCEPTED",
        src.is_file() and accepted.is_file() and sha256(src) == sha256(accepted),
    )

    python = pytest_python(project)
    env = {**os.environ, "PYTHONPATH": str(project), "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1"}
    result = subprocess.run(
        [python, "-m", "pytest", "-q"],
        cwd=project,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    print(result.stdout)
    print("PYTEST_PYTHON", python)
    print("PYTEST_EXIT_CODE", result.returncode)
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
