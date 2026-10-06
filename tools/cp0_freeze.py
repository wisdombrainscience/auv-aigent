#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shlex
import subprocess
import sys
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Iterable

BASELINE_ID = "RUAV-AI-ARCH-V8.0-BL1"
DEFAULT_BASELINE = Path("docs/architecture/REMOTE_UAV_ARCHITECTURE_BASELINE_V8_0_FROZEN.md")
SCHEMA_VERSION = 1

EXCLUDED_DIR_NAMES = {
    ".git", ".venv", "venv", "__pycache__", ".pytest_cache", ".mypy_cache",
    ".ruff_cache", ".tox", ".nox", "node_modules", "runs", ".runtime",
}
EXCLUDED_FILE_SUFFIXES = {".pyc", ".pyo"}
EXCLUDED_TOPLEVEL_PREFIXES = {
    "reports/cp0/",
}


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def read_project_version(project: Path) -> str | None:
    pyproject = project / "pyproject.toml"
    if not pyproject.is_file():
        return None
    text = pyproject.read_text(encoding="utf-8", errors="replace")
    # Keep this dependency-free for Python 3.10 compatibility.
    project_match = re.search(r"(?ms)^\[project\]\s*(.*?)(?=^\[|\Z)", text)
    scope = project_match.group(1) if project_match else text
    version_match = re.search(r'(?m)^\s*version\s*=\s*["\']([^"\']+)["\']\s*$', scope)
    return version_match.group(1) if version_match else None


def git_commit(project: Path) -> str | None:
    try:
        cp = subprocess.run(
            ["git", "-C", str(project), "rev-parse", "HEAD"],
            text=True,
            capture_output=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    value = cp.stdout.strip()
    return value if cp.returncode == 0 and value else None


def is_excluded(rel: PurePosixPath) -> bool:
    parts = rel.parts
    if any(part in EXCLUDED_DIR_NAMES for part in parts[:-1]):
        return True
    if rel.suffix.lower() in EXCLUDED_FILE_SUFFIXES:
        return True
    rel_s = rel.as_posix()
    if any(rel_s.startswith(prefix) for prefix in EXCLUDED_TOPLEVEL_PREFIXES):
        return True
    if rel_s.endswith("~") or rel.name in {".DS_Store"}:
        return True
    return False


def iter_snapshot_files(project: Path, output_dir: Path) -> tuple[list[Path], list[str]]:
    result: list[Path] = []
    warnings: list[str] = []
    output_resolved = output_dir.resolve()
    for root, dirs, files in os.walk(project):
        root_p = Path(root)
        dirs[:] = sorted(
            d for d in dirs
            if d not in EXCLUDED_DIR_NAMES
            and not (root_p / d).resolve().is_relative_to(output_resolved)
        )
        for name in sorted(files):
            path = root_p / name
            if path.is_symlink():
                warnings.append(f"skipped_symlink:{path.relative_to(project).as_posix()}")
                continue
            rel = PurePosixPath(path.relative_to(project).as_posix())
            if is_excluded(rel):
                continue
            try:
                if path.resolve().is_relative_to(output_resolved):
                    continue
            except OSError:
                warnings.append(f"unresolvable:{rel.as_posix()}")
                continue
            result.append(path)
    result.sort(key=lambda p: p.relative_to(project).as_posix())
    return result, warnings


def build_source_manifest(project: Path, files: Iterable[Path]) -> tuple[list[dict], str]:
    rows: list[dict] = []
    for path in files:
        rel = path.relative_to(project).as_posix()
        rows.append({"path": rel, "size": path.stat().st_size, "sha256": sha256_file(path)})
    encoded = json.dumps(rows, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return rows, sha256_bytes(encoded)


def create_reproducible_zip(project: Path, files: Iterable[Path], destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for path in files:
            rel = path.relative_to(project).as_posix()
            data = path.read_bytes()
            info = zipfile.ZipInfo(rel, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 3
            mode = 0o755 if os.access(path, os.X_OK) else 0o644
            info.external_attr = mode << 16
            zf.writestr(info, data, compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)


def run_command(name: str, command: str | None, project: Path, log_path: Path, timeout_s: int) -> dict:
    if not command:
        return {"name": name, "status": "NOT_RUN", "command": None, "returncode": None, "duration_s": 0.0, "log": log_path.name}
    start = time.monotonic()
    try:
        cp = subprocess.run(
            command,
            cwd=project,
            shell=True,
            executable="/bin/bash",
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout_s,
            check=False,
            env=os.environ.copy(),
        )
        output = cp.stdout or ""
        status = "PASS" if cp.returncode == 0 else "FAIL"
        returncode = cp.returncode
    except subprocess.TimeoutExpired as exc:
        output = (exc.stdout or "") if isinstance(exc.stdout, str) else ""
        output += f"\nCP0_TOOL_TIMEOUT after {timeout_s}s\n"
        status = "TIMEOUT"
        returncode = None
    except Exception as exc:  # Operational failure must be explicit, never hidden.
        output = f"CP0_TOOL_ERROR {type(exc).__name__}: {exc}\n"
        status = "ERROR"
        returncode = None
    duration = time.monotonic() - start
    log_path.write_text(output, encoding="utf-8")
    return {
        "name": name,
        "status": status,
        "command": command,
        "returncode": returncode,
        "duration_s": round(duration, 3),
        "log": log_path.name,
    }


def auto_regression_command(project: Path) -> str | None:
    if (project / "uavctl").is_file():
        return "./uavctl test"
    if (project / "test-uav").is_file():
        return "./test-uav"
    return None


def default_single_flight_command(project: Path) -> str | None:
    helper = project / "tools/cp0_single_flight_acceptance.py"
    python = project / ".venv/bin/python"
    if not (helper.is_file() and python.is_file()):
        return None
    return ".venv/bin/python tools/cp0_single_flight_acceptance.py --project ."


def default_mission_command(project: Path) -> str | None:
    exe = project / "mission-uav"
    mission_file = project / "config/examples/mission_reference.json"
    if not (exe.is_file() and mission_file.is_file()):
        return None
    return (
        "./mission-uav --mission-file config/examples/mission_reference.json "
        "--scenario CP0-MISSION "
        "--reset-simulation --spawn-north 0 --spawn-east 0 --spawn-altitude 0 "
        "--stop-simulation-after"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Freeze and verify Remote UAV CP0 source baseline.")
    parser.add_argument("--project", default=".", help="Project root (default: current directory)")
    parser.add_argument("--baseline", default=str(DEFAULT_BASELINE), help="Baseline markdown path relative to project")
    parser.add_argument("--output", default=None, help="Output directory; default reports/cp0/<timestamp>")
    parser.add_argument("--regression-cmd", default=None, help="Override regression command")
    parser.add_argument("--single-flight-cmd", default=None, help="Override single-flight acceptance command")
    parser.add_argument("--mission-cmd", default=None, help="Override mission acceptance command")
    parser.add_argument("--run-acceptance", action="store_true", help="Run acceptance commands. Without this, acceptance stays NOT_RUN.")
    parser.add_argument("--timeout-s", type=int, default=900, help="Per-command timeout in seconds")
    parser.add_argument("--skip-regression", action="store_true", help="Do not run regression; CP0 cannot become GREEN")
    parser.add_argument("--no-archive", action="store_true", help="Do not create source archive; CP0 cannot become GREEN")
    args = parser.parse_args()

    project = Path(args.project).expanduser().resolve()
    if not project.is_dir():
        parser.error(f"project is not a directory: {project}")

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output = (Path(args.output).expanduser() if args.output else project / "reports" / "cp0" / stamp).resolve()
    output.mkdir(parents=True, exist_ok=True)

    baseline = Path(args.baseline)
    if not baseline.is_absolute():
        baseline = project / baseline
    baseline = baseline.resolve()

    reasons: list[str] = []
    warnings: list[str] = []

    if not baseline.is_file():
        reasons.append("baseline_file_missing")
        baseline_sha = None
    else:
        baseline_sha = sha256_file(baseline)
        if BASELINE_ID not in baseline.read_text(encoding="utf-8", errors="replace"):
            reasons.append("baseline_id_not_found_in_baseline_file")

    files, snapshot_warnings = iter_snapshot_files(project, output)
    warnings.extend(snapshot_warnings)
    rows, source_manifest_sha = build_source_manifest(project, files)
    source_manifest_path = output / "source_manifest.json"
    source_manifest_path.write_text(json.dumps(rows, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    archive_path: Path | None = None
    archive_sha: str | None = None
    if args.no_archive:
        reasons.append("source_archive_not_created")
    else:
        archive_path = output / f"remote_uav_cp0_source_{stamp}.zip"
        create_reproducible_zip(project, files, archive_path)
        archive_sha = sha256_file(archive_path)

    regression_cmd = None if args.skip_regression else (args.regression_cmd or auto_regression_command(project))
    regression = run_command("regression", regression_cmd, project, output / "regression.log", args.timeout_s)
    if regression["status"] != "PASS":
        reasons.append(f"regression_{regression['status'].lower()}")

    if args.run_acceptance:
        single_cmd = args.single_flight_cmd or default_single_flight_command(project)
        mission_cmd = args.mission_cmd or default_mission_command(project)
    else:
        single_cmd = None
        mission_cmd = None

    single = run_command("single_flight", single_cmd, project, output / "single_flight.log", args.timeout_s)
    mission = run_command("mission", mission_cmd, project, output / "mission.log", args.timeout_s)

    if single["status"] != "PASS":
        reasons.append(f"single_flight_{single['status'].lower()}")
    if mission["status"] != "PASS":
        reasons.append(f"mission_{mission['status'].lower()}")

    manifest = {
        "schema_version": SCHEMA_VERSION,
        "baseline_id": BASELINE_ID,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "project": {
            "path": str(project),
            "version": read_project_version(project),
            "git_commit": git_commit(project),
        },
        "baseline": {
            "path": str(baseline),
            "sha256": baseline_sha,
        },
        "source_snapshot": {
            "archive": archive_path.name if archive_path else None,
            "archive_sha256": archive_sha,
            "file_count": len(rows),
            "manifest": source_manifest_path.name,
            "manifest_sha256": source_manifest_sha,
        },
        "checks": {
            "regression": regression,
            "single_flight": single,
            "mission": mission,
        },
        "warnings": sorted(set(warnings)),
        "cp0": {
            "status": "GREEN" if not reasons else "RED",
            "reasons": sorted(set(reasons)),
        },
    }
    manifest_path = output / "cp0_manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(f"CP0_OUTPUT={output}")
    print(f"CP0_STATUS={manifest['cp0']['status']}")
    print(f"PROJECT_VERSION={manifest['project']['version'] or 'UNKNOWN'}")
    print(f"GIT_COMMIT={manifest['project']['git_commit'] or 'N/A'}")
    print(f"SOURCE_MANIFEST_SHA256={source_manifest_sha}")
    print(f"SOURCE_ARCHIVE_SHA256={archive_sha or 'N/A'}")
    print(f"BASELINE_SHA256={baseline_sha or 'N/A'}")
    if reasons:
        print("CP0_REASONS=" + ",".join(sorted(set(reasons))))
    return 0 if manifest["cp0"]["status"] == "GREEN" else 2


if __name__ == "__main__":
    raise SystemExit(main())
