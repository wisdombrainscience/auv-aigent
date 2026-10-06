#!/usr/bin/env python3
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import urllib.error
import urllib.request

MODEL = "qwen2.5-coder:3b"
OLLAMA_TAGS = "http://127.0.0.1:11434/api/tags"


def command_version(command: list[str]) -> tuple[bool, str]:
    exe = shutil.which(command[0])
    if not exe:
        return False, "not found"
    try:
        p = subprocess.run(command, text=True, capture_output=True, timeout=10)
        text = (p.stdout or p.stderr).strip().splitlines()
        return p.returncode == 0, text[0] if text else f"exit={p.returncode}"
    except Exception as exc:
        return False, str(exc)


def ollama_models() -> tuple[bool, list[str] | str]:
    try:
        with urllib.request.urlopen(OLLAMA_TAGS, timeout=5) as r:
            payload = json.load(r)
        names = [m.get("name", "") for m in payload.get("models", [])]
        return True, names
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        return False, str(exc)


def main() -> int:
    checks: list[tuple[str, bool, str]] = []

    ok, detail = command_version(["ollama", "--version"])
    checks.append(("Ollama CLI", ok, detail))

    api_ok, models_or_error = ollama_models()
    checks.append(("Ollama API", api_ok, "reachable" if api_ok else str(models_or_error)))

    model_ok = api_ok and MODEL in models_or_error
    checks.append(("Baseline model", bool(model_ok), MODEL if model_ok else f"missing: {MODEL}"))

    ok, detail = command_version(["opencode", "--version"])
    checks.append(("OpenCode CLI", ok, detail))

    print("LOCAL AI ENVIRONMENT CHECK")
    print("=" * 60)
    for name, passed, detail in checks:
        print(f"{'PASS' if passed else 'FAIL':4}  {name:16}  {detail}")

    overall = all(passed for _, passed, _ in checks)
    print("=" * 60)
    print("OVERALL:", "PASS" if overall else "FAIL")
    return 0 if overall else 1


if __name__ == "__main__":
    raise SystemExit(main())
