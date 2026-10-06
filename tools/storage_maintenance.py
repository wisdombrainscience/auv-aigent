#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import time
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]

from src.resources.manager import ResourceManager, load_resource_policy
from src.runtime_paths import runs_root as configured_runs_root


@dataclass(frozen=True)
class AttemptInfo:
    path: Path
    task_id: str
    scenario_id: str
    verdict: str | None
    finalized: bool
    mtime: float
    bytes: int
    pinned: bool


def _tree_size(path: Path) -> int:
    total = 0
    try:
        items = path.rglob("*")
        for item in items:
            try:
                if item.is_file():
                    total += item.stat().st_size
            except OSError:
                pass
    except OSError:
        pass
    return total


def _load_pins(runs_root: Path) -> set[str]:
    path = runs_root / ".retention_pins.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        return set()
    values = data.get("attempt_dirs", []) if isinstance(data, dict) else []
    return {str(Path(v).resolve()) for v in values if isinstance(v, str)}


def _save_pins(runs_root: Path, pins: set[str]) -> None:
    runs_root.mkdir(parents=True, exist_ok=True)
    path = runs_root / ".retention_pins.json"
    payload = {"attempt_dirs": sorted(pins)}
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _sqlite_pins(runs_root: Path) -> set[str]:
    db = runs_root / "index.sqlite3"
    if not db.is_file():
        return set()
    try:
        with sqlite3.connect(db) as con:
            rows = con.execute("SELECT attempt_dir FROM runs WHERE pinned = 1").fetchall()
        return {str(Path(row[0]).resolve()) for row in rows if row and row[0]}
    except sqlite3.Error:
        return set()


def _verdict(path: Path) -> str | None:
    verification = path / "verification.json"
    if not verification.is_file():
        return None
    try:
        data = json.loads(verification.read_text(encoding="utf-8"))
        value = str(data.get("overall") or "").upper()
        return value if value in {"PASS", "FAIL"} else None
    except (OSError, json.JSONDecodeError, TypeError):
        return None


def scan_attempts(runs_root: Path) -> list[AttemptInfo]:
    explicit_pins = _load_pins(runs_root)
    pinned = explicit_pins | _sqlite_pins(runs_root)
    attempts: list[AttemptInfo] = []
    if not runs_root.is_dir():
        return attempts
    for path in runs_root.glob("*/*/attempt_*"):
        if not path.is_dir():
            continue
        try:
            stat = (path / "finalized.json").stat() if (path / "finalized.json").is_file() else path.stat()
        except OSError:
            continue
        try:
            rel = path.relative_to(runs_root)
            task_id, scenario_id = rel.parts[0], rel.parts[1]
        except (ValueError, IndexError):
            continue
        attempts.append(
            AttemptInfo(
                path=path.resolve(),
                task_id=task_id,
                scenario_id=scenario_id,
                verdict=_verdict(path),
                finalized=(path / "finalized.json").is_file(),
                mtime=stat.st_mtime,
                bytes=_tree_size(path),
                pinned=str(path.resolve()) in pinned,
            )
        )
    return attempts


def cleanup_candidates(project: Path, now: float | None = None) -> list[AttemptInfo]:
    runs_root = configured_runs_root(project)
    policy = load_resource_policy(project / "config" / "resource_policy.yaml")
    now = time.time() if now is None else now
    attempts = scan_attempts(runs_root)
    groups: dict[tuple[str, str], list[AttemptInfo]] = defaultdict(list)
    for item in attempts:
        groups[(item.task_id, item.scenario_id)].append(item)
    keep: set[str] = set()
    for items in groups.values():
        items.sort(key=lambda x: x.mtime, reverse=True)
        for item in items[: policy.retention_keep_latest_per_scenario]:
            keep.add(str(item.path))

    candidates: list[AttemptInfo] = []
    for item in attempts:
        if item.pinned or str(item.path) in keep:
            continue
        days = (now - item.mtime) / 86400.0
        if item.verdict == "PASS":
            threshold = policy.retention_pass_days
        elif item.verdict == "FAIL":
            threshold = policy.retention_fail_days
        else:
            threshold = policy.retention_incomplete_days
        if days >= threshold:
            candidates.append(item)
    return sorted(candidates, key=lambda x: x.mtime)


def _delete_index_rows(runs_root: Path, deleted: list[AttemptInfo]) -> None:
    db = runs_root / "index.sqlite3"
    if not db.is_file() or not deleted:
        return
    try:
        with sqlite3.connect(db) as con:
            con.executemany(
                "DELETE FROM runs WHERE attempt_dir = ?",
                [(str(item.path),) for item in deleted],
            )
    except sqlite3.Error:
        pass


def cmd_status(project: Path) -> int:
    policy = load_resource_policy(project / "config" / "resource_policy.yaml")
    manager = ResourceManager(configured_runs_root(project), policy)
    status = manager.status()
    attempts = scan_attempts(configured_runs_root(project))
    by_task: dict[str, int] = defaultdict(int)
    by_task_bytes: dict[str, int] = defaultdict(int)
    pinned = 0
    for item in attempts:
        by_task[item.task_id] += 1
        by_task_bytes[item.task_id] += item.bytes
        pinned += int(item.pinned)
    print(json.dumps({
        "resource": status,
        "attempts": len(attempts),
        "pinned_attempts": pinned,
        "by_task": {
            task: {"attempts": by_task[task], "mb": round(by_task_bytes[task] / 1048576, 3)}
            for task in sorted(by_task)
        },
    }, indent=2))
    return 0


def cmd_cleanup(project: Path, apply: bool) -> int:
    candidates = cleanup_candidates(project)
    total = sum(item.bytes for item in candidates)
    mode = "APPLY" if apply else "DRY_RUN"
    print(f"RETENTION_{mode} candidates={len(candidates)} reclaim_mb={total/1048576:.3f}")
    for item in candidates[:200]:
        age_days = (time.time() - item.mtime) / 86400.0
        print(f"{item.verdict or 'INCOMPLETE':10} {age_days:7.1f}d {item.bytes/1048576:9.3f}MB {item.path}")
    if len(candidates) > 200:
        print(f"... {len(candidates)-200} more candidates omitted")
    if not apply:
        print("No files removed. Re-run with --apply after reviewing the list.")
        return 0
    deleted: list[AttemptInfo] = []
    for item in candidates:
        try:
            shutil.rmtree(item.path)
            deleted.append(item)
        except OSError as exc:
            print(f"WARN could not remove {item.path}: {exc}")
    _delete_index_rows(configured_runs_root(project), deleted)
    print(f"RETENTION_APPLIED deleted={len(deleted)} reclaimed_mb={sum(x.bytes for x in deleted)/1048576:.3f}")
    return 0


def cmd_pin(project: Path, attempt: str, pin: bool) -> int:
    runs_root = configured_runs_root(project).resolve()
    path = Path(attempt).expanduser()
    if not path.is_absolute():
        path = (project / path).resolve()
    else:
        path = path.resolve()
    try:
        path.relative_to(runs_root)
    except ValueError:
        raise SystemExit("attempt must be inside the project's runs directory")
    if not path.is_dir() or not path.name.startswith("attempt_"):
        raise SystemExit(f"attempt directory not found: {path}")
    pins = _load_pins(runs_root)
    if pin:
        pins.add(str(path))
    else:
        pins.discard(str(path))
    _save_pins(runs_root, pins)
    print(("PINNED " if pin else "UNPINNED ") + str(path))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Safe storage/retention maintenance")
    ap.add_argument("--project", type=Path, default=PROJECT_ROOT)
    sub = ap.add_subparsers(dest="command", required=True)
    sub.add_parser("status")
    cleanup = sub.add_parser("cleanup")
    cleanup.add_argument("--apply", action="store_true", help="delete listed unpinned candidates")
    pin = sub.add_parser("pin")
    pin.add_argument("attempt")
    unpin = sub.add_parser("unpin")
    unpin.add_argument("attempt")
    args = ap.parse_args()
    project = args.project.expanduser().resolve()
    if args.command == "status":
        return cmd_status(project)
    if args.command == "cleanup":
        return cmd_cleanup(project, bool(args.apply))
    if args.command == "pin":
        return cmd_pin(project, args.attempt, True)
    if args.command == "unpin":
        return cmd_pin(project, args.attempt, False)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
