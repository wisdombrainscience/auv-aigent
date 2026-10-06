#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
import subprocess
from pathlib import Path

EXCLUDED_PARTS = {
    ".git", ".venv", "venv", "env", "runs", "reports", "dist",
    "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache",
}
EXCLUDED_SUFFIXES = {".pyc", ".pyo", ".zip", ".patch", ".log"}
FORBIDDEN_NAMES = {
    "uav.env", ".env", "id_rsa", "id_ed25519", "credentials.json", "secrets.json",
}
SECRET_PATTERNS = [
    ("GitHub token", re.compile(r"\bghp_[A-Za-z0-9]{30,}\b")),
    ("GitHub fine-grained token", re.compile(r"\bgithub_pat_[A-Za-z0-9_]{30,}\b")),
    ("AWS access key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("Private key", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")),
]


def candidate_files(project: Path) -> list[Path]:
    git_dir = project / ".git"
    if git_dir.exists():
        cp = subprocess.run(
            ["git", "ls-files", "-z"], cwd=project, check=False,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        )
        if cp.returncode == 0:
            return [project / p.decode() for p in cp.stdout.split(b"\0") if p]
    files: list[Path] = []
    for p in project.rglob("*"):
        if not p.is_file():
            continue
        rel = p.relative_to(project)
        if any(part in EXCLUDED_PARTS for part in rel.parts):
            continue
        if p.suffix in EXCLUDED_SUFFIXES:
            continue
        if p.name in FORBIDDEN_NAMES:
            continue
        files.append(p)
    return files


def scan(project: Path) -> tuple[list[str], int]:
    issues: list[str] = []
    files = candidate_files(project)
    required = [
        "README.md", ".gitignore", ".github/workflows/ci.yml",
        "docs/GITHUB_PUBLISHING.md", "SECURITY.md", "CONTRIBUTING.md",
    ]
    for rel in required:
        if not (project / rel).is_file():
            issues.append(f"missing required GitHub file: {rel}")
    for p in files:
        rel = p.relative_to(project)
        if p.name in FORBIDDEN_NAMES or p.suffix in {".pem", ".key"}:
            issues.append(f"sensitive file is tracked/candidate: {rel}")
            continue
        try:
            if p.stat().st_size > 2_000_000:
                continue
            text = p.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for label, rx in SECRET_PATTERNS:
            if rx.search(text):
                issues.append(f"possible {label} in {rel}")
    return issues, len(files)


def main() -> int:
    ap = argparse.ArgumentParser(description="Check a Remote UAV source tree before GitHub push")
    ap.add_argument("--project", type=Path, default=Path(__file__).resolve().parents[1])
    args = ap.parse_args()
    project = args.project.resolve()
    issues, count = scan(project)
    print(f"PROJECT={project}")
    print(f"FILES_SCANNED={count}")
    if issues:
        for item in issues:
            print(f"FAIL {item}")
        print(f"GITHUB_PREFLIGHT=FAIL issues={len(issues)}")
        return 1
    print("GITHUB_PREFLIGHT=PASS issues=0")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
