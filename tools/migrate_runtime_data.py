#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import shutil
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]

from src.runtime_paths import runs_root


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser(description="Safely migrate legacy <project>/runs to UAV_DATA_DIR/runs")
    ap.add_argument("--project", type=Path, default=PROJECT)
    ap.add_argument("--source", type=Path, default=None, help="old project directory or old runs directory")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    project = args.project.expanduser().resolve()
    source = (args.source.expanduser().resolve() if args.source else project / "runs")
    if source.name != "runs":
        source = source / "runs"
    target = runs_root(project).resolve()
    if source.resolve() == target:
        print(f"MIGRATION_NOT_NEEDED source=target={target}")
        return 0
    if not source.is_dir():
        print(f"MIGRATION_SOURCE_EMPTY {source}")
        return 0
    files = [p for p in source.rglob("*") if p.is_file() and ".runtime" not in p.relative_to(source).parts]
    bytes_total = sum(p.stat().st_size for p in files)
    conflicts: list[Path] = []
    copy_items: list[tuple[Path, Path]] = []
    for src in files:
        dst = target / src.relative_to(source)
        if dst.exists():
            try:
                if dst.is_file() and dst.stat().st_size == src.stat().st_size and sha256(dst) == sha256(src):
                    continue
            except OSError:
                pass
            conflicts.append(dst)
        else:
            copy_items.append((src, dst))
    print(f"MIGRATION_PLAN source={source} target={target} files={len(files)} copy={len(copy_items)} conflicts={len(conflicts)} mb={bytes_total/1048576:.3f}")
    if conflicts:
        for path in conflicts[:20]:
            print(f"CONFLICT {path}")
        print("Migration aborted: target has conflicting files. Keep both data sets separate or resolve conflicts first.")
        return 2
    if not args.apply:
        print("DRY_RUN: no files copied. Re-run with --apply.")
        return 0
    for src, dst in copy_items:
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
    print(f"MIGRATION_APPLIED copied={len(copy_items)} target={target}")
    print("Source data was not deleted. Remove the old project only after verifying the new installation.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
