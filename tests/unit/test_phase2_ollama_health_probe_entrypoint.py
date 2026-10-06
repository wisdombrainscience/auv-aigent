from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys


def project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def test_direct_probe_invocation_can_import_project_ai_without_pythonpath() -> None:
    root = project_root()
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)

    result = subprocess.run(
        [
            sys.executable,
            "tools/check_ollama_health.py",
            "--help",
        ],
        cwd=root,
        env=env,
        text=True,
        capture_output=True,
        timeout=5,
    )

    assert result.returncode == 0, result.stderr
    assert "Bounded local-only Ollama health probe" in result.stdout


def test_direct_probe_import_bootstrap_is_repo_local_only() -> None:
    path = project_root() / "tools/check_ollama_health.py"
    text = path.read_text(encoding="utf-8")

    assert "Path(__file__).resolve().parents[1]" in text
    assert "sys.path.insert(0, str(_PROJECT_ROOT))" in text
    assert "os.environ" not in text.split("from ai.providers.ollama", 1)[0]


def test_probe_still_uses_bounded_provider_health_only() -> None:
    path = project_root() / "tools/check_ollama_health.py"
    text = path.read_text(encoding="utf-8")

    assert ".health()" in text
    assert ".generate(" not in text
    assert ".list_models(" not in text
    assert "while True" not in text
    assert "time.sleep" not in text
    assert "asyncio.sleep" not in text
