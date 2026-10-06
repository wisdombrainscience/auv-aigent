#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path


HEADER_RE = re.compile(r"^(---|\+\+\+)\s+([^\t\n]+)")


def normalize_patch_path(raw: str) -> str | None:
    raw = raw.strip()
    if raw == "/dev/null":
        return None
    if raw.startswith("a/") or raw.startswith("b/"):
        raw = raw[2:]
    p = Path(raw)
    if p.is_absolute() or ".." in p.parts:
        raise ValueError(f"unsafe patch path: {raw}")
    return p.as_posix()


def patch_paths(text: str) -> set[str]:
    paths = set()
    for line in text.splitlines():
        m = HEADER_RE.match(line)
        if not m:
            continue
        p = normalize_patch_path(m.group(2))
        if p:
            paths.add(p)
    return paths


def run(cmd: list[str], cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd,
        cwd=str(cwd),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", type=Path, default=Path.cwd())
    ap.add_argument("--base-candidate", type=Path, required=True)
    ap.add_argument("--patch", type=Path, required=True)
    ap.add_argument("--output-candidate", type=Path, required=True)
    ap.add_argument(
        "--contract",
        type=Path,
        default=Path("specs/NAV-001-IMPLEMENTATION-CONTRACT.yaml"),
    )
    args = ap.parse_args()

    project = args.project.expanduser().resolve()
    base_candidate = args.base_candidate.expanduser().resolve()
    patch_file = args.patch.expanduser().resolve()
    output_candidate = args.output_candidate.expanduser().resolve()
    contract_path = (
        args.contract if args.contract.is_absolute() else project / args.contract
    ).resolve()

    if not base_candidate.is_file():
        raise SystemExit(f"base candidate missing: {base_candidate}")
    if not patch_file.is_file():
        raise SystemExit(f"patch missing: {patch_file}")
    if not contract_path.is_file():
        raise SystemExit(f"contract missing: {contract_path}")
    if shutil.which("patch") is None:
        raise SystemExit("system command 'patch' is required")

    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    allowed = set(contract["writable_paths"])
    patch_text = patch_file.read_text(encoding="utf-8")
    paths = patch_paths(patch_text)

    if not paths:
        raise SystemExit("patch contains no file headers")
    if not paths.issubset(allowed):
        raise SystemExit(
            f"BOUNDARY FAIL: patch paths={sorted(paths)} allowed={sorted(allowed)}"
        )

    target_rel = Path("src/capabilities/navigation/go_to_target.py")
    if target_rel.as_posix() not in paths:
        raise SystemExit("patch does not target GoToTargetModule")

    with tempfile.TemporaryDirectory(prefix="nav001_patch_") as td:
        ws = Path(td)
        target = ws / target_rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(base_candidate, target)

        dry = run(["patch", "--dry-run", "-p1", "-i", str(patch_file)], ws)
        if dry.returncode != 0:
            print(dry.stdout)
            raise SystemExit("PATCH DRY-RUN: FAIL")

        applied = run(["patch", "-p1", "-i", str(patch_file)], ws)
        if applied.returncode != 0:
            print(applied.stdout)
            raise SystemExit("PATCH APPLY: FAIL")

        output_candidate.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(target, output_candidate)

    print("PATCH BOUNDARY: PASS")
    print("PATCH APPLY: PASS")
    print(f"OUTPUT={output_candidate}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
