from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any


class EvidenceIndex:
    """Small persistent metadata index; raw telemetry remains in evidence files."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path, timeout=5)
        connection.row_factory = sqlite3.Row
        return connection

    def _init_schema(self) -> None:
        with self._connect() as con:
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS runs (
                    run_id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL,
                    scenario_id TEXT,
                    attempt_dir TEXT NOT NULL,
                    status TEXT,
                    verdict TEXT,
                    position_error_m REAL,
                    elapsed_time_s REAL,
                    target_json TEXT,
                    final_json TEXT,
                    created_at TEXT,
                    finished_at TEXT,
                    pinned INTEGER NOT NULL DEFAULT 0
                )
                """
            )
            con.execute("CREATE INDEX IF NOT EXISTS runs_finished_idx ON runs(finished_at DESC)")

    def upsert(self, record: dict[str, Any]) -> None:
        target = record.get("target_position_m")
        final = record.get("final_position_m")
        values = (
            str(record["run_id"]),
            str(record.get("task_id") or "NAV-001"),
            record.get("scenario_id"),
            str(record.get("attempt_dir") or ""),
            record.get("status"),
            record.get("verdict"),
            record.get("position_error_m"),
            record.get("elapsed_time_s"),
            json.dumps(target) if target is not None else None,
            json.dumps(final) if final is not None else None,
            record.get("created_at"),
            record.get("finished_at"),
            1 if record.get("pinned") else 0,
        )
        with self._connect() as con:
            con.execute(
                """
                INSERT INTO runs (
                    run_id, task_id, scenario_id, attempt_dir, status, verdict,
                    position_error_m, elapsed_time_s, target_json, final_json,
                    created_at, finished_at, pinned
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(run_id) DO UPDATE SET
                    status=excluded.status,
                    verdict=excluded.verdict,
                    position_error_m=excluded.position_error_m,
                    elapsed_time_s=excluded.elapsed_time_s,
                    final_json=excluded.final_json,
                    finished_at=excluded.finished_at,
                    pinned=excluded.pinned
                """,
                values,
            )

    @staticmethod
    def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
        out = dict(row)
        for source, dest in (("target_json", "target_position_m"), ("final_json", "final_position_m")):
            raw = out.pop(source, None)
            out[dest] = json.loads(raw) if raw else None
        out["pinned"] = bool(out.get("pinned"))
        return out

    def list_recent(self, limit: int = 50, offset: int = 0) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 200))
        offset = max(0, int(offset))
        with self._connect() as con:
            rows = con.execute(
                "SELECT * FROM runs ORDER BY COALESCE(finished_at, created_at) DESC LIMIT ? OFFSET ?",
                (limit, offset),
            ).fetchall()
        return [self._row_to_dict(row) for row in rows]


    def _indexed_attempt_dirs(self, task_id: str) -> set[str]:
        with self._connect() as con:
            rows = con.execute(
                "SELECT attempt_dir FROM runs WHERE task_id = ?", (str(task_id),)
            ).fetchall()
        return {str(row["attempt_dir"]) for row in rows if row["attempt_dir"]}

    def backfill_from_runs(self, task_root: Path) -> int:
        """Index only missing finalized/verified attempts without telemetry reads.

        Older versions re-read every attempt JSON file on each server startup.
        With long-lived evidence stores this made startup I/O grow linearly.
        Existing attempt directories are now skipped from SQLite metadata alone.
        """
        task_root = Path(task_root)
        if not task_root.exists():
            return 0
        indexed = self._indexed_attempt_dirs(task_root.name)
        added = 0
        for verification_path in task_root.glob("*/attempt_*/verification.json"):
            attempt_dir = verification_path.parent
            if str(attempt_dir) in indexed:
                continue
            try:
                verification = json.loads(verification_path.read_text(encoding="utf-8"))
                input_data = {}
                input_path = attempt_dir / "input.json"
                if input_path.is_file():
                    input_data = json.loads(input_path.read_text(encoding="utf-8"))
                final = None
                output_path = attempt_dir / "module_output.json"
                if output_path.is_file():
                    output = json.loads(output_path.read_text(encoding="utf-8"))
                    data = output.get("data") if isinstance(output, dict) else None
                    candidate = data.get("final_position") if isinstance(data, dict) else None
                    if isinstance(candidate, list) and len(candidate) == 3:
                        final = candidate
                scenario_id = attempt_dir.parent.name
                stat = verification_path.stat()
                run_id = str(attempt_dir.relative_to(task_root.parent))
                self.upsert(
                    {
                        "run_id": run_id,
                        "task_id": task_root.name,
                        "scenario_id": scenario_id,
                        "attempt_dir": str(attempt_dir),
                        "status": verification.get("overall"),
                        "verdict": verification.get("overall"),
                        "position_error_m": verification.get("position_error_m"),
                        "elapsed_time_s": verification.get("elapsed_time_s"),
                        "target_position_m": input_data.get("target_position_m"),
                        "final_position_m": final,
                        "created_at": None,
                        "finished_at": __import__("datetime").datetime.fromtimestamp(
                            stat.st_mtime, tz=__import__("datetime").timezone.utc
                        ).isoformat(),
                    }
                )
                added += 1
            except (OSError, ValueError, TypeError, json.JSONDecodeError):
                continue
        return added

    def count(self) -> int:
        with self._connect() as con:
            row = con.execute("SELECT COUNT(*) AS n FROM runs").fetchone()
        return int(row["n"])
