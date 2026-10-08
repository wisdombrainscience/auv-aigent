#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import zipfile
from datetime import datetime, timezone
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
EXCLUDE_NAMES = {
    ".git",
    ".venv",
    ".pytest_cache",
    "__pycache__",
    "runs",
    "reports",
    ".mypy_cache",
    ".ruff_cache",
}
EXCLUDE_SUFFIXES = {".pyc", ".pyo", ".rej", ".orig"}


def version(project: Path) -> str:
    try:
        import tomllib
    except ModuleNotFoundError:
        import tomli as tomllib

    return str(
        tomllib.loads(
            (project / "pyproject.toml").read_text(encoding="utf-8")
        )["project"]["version"]
    )

def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def include_file(project: Path, path: Path) -> bool:
    rel = path.relative_to(project)
    if rel.as_posix() == "RELEASE_MANIFEST.json":
        return False
    if any(part in EXCLUDE_NAMES for part in rel.parts):
        return False
    if path.suffix in EXCLUDE_SUFFIXES:
        return False
    if path.name == "uav.env":
        return False
    if path.name.endswith(".zip") or path.name.endswith(".patch"):
        return False
    return path.is_file()


def build(project: Path, output_dir: Path) -> tuple[Path, Path]:
    project = project.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    ver = version(project)
    release_name = f"remote_UAV_v{ver.replace('.', '_')}_deploy"
    staging = output_dir / release_name
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir()
    manifest_files: list[dict[str, object]] = []
    for src in sorted(project.rglob("*")):
        if not include_file(project, src):
            continue
        rel = src.relative_to(project)
        dst = staging / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        manifest_files.append(
            {"path": str(rel), "bytes": src.stat().st_size, "sha256": sha256(src)}
        )
    manifest = {
        "product": "Remote UAV Mission Console",
        "version": ver,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source_files": len(manifest_files),
        "files": manifest_files,
        "excluded_runtime_data": [".venv", "runs", "reports", "caches", "uav.env"],
    }
    (staging / "RELEASE_MANIFEST.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    zip_path = output_dir / f"{release_name}.zip"
    zip_path.unlink(missing_ok=True)
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for src in sorted(staging.rglob("*")):
            if src.is_file():
                z.write(src, Path(release_name) / src.relative_to(staging))
    sum_path = zip_path.with_suffix(zip_path.suffix + ".sha256")
    sum_path.write_text(f"{sha256(zip_path)}  {zip_path.name}\n", encoding="utf-8")
    shutil.rmtree(staging)
    return zip_path, sum_path


def main() -> int:
    ap = argparse.ArgumentParser(description="Build a clean redistributable Remote UAV zip")
    ap.add_argument("--project", type=Path, default=PROJECT)
    ap.add_argument("--output-dir", type=Path, default=PROJECT / "dist")
    args = ap.parse_args()
    zip_path, sum_path = build(args.project, args.output_dir)
    print(f"RELEASE={zip_path}")
    print(f"SHA256={sum_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
