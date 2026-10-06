from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys


def project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def test_model_manager_probe_direct_invocation_imports_without_pythonpath() -> None:
    root = project_root()
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)

    result = subprocess.run(
        [sys.executable, "tools/check_model_manager.py", "--help"],
        cwd=root,
        env=env,
        text=True,
        capture_output=True,
        timeout=5,
    )

    assert result.returncode == 0, result.stderr
    assert "Minimal local Model Manager acceptance probe" in result.stdout
