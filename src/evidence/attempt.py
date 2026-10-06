from __future__ import annotations

import hashlib
import json
import os
import platform
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class EvidenceFinalizedError(RuntimeError):
    pass


class AttemptEvidence:
    """
    Deterministic evidence manager for one experiment attempt.

    Layout:
      runs/<task>/<scenario>/attempt_<NNN>/
    """

    def __init__(
        self,
        root: Path,
        task_id: str,
        scenario_id: str,
        attempt: int,
    ) -> None:
        if attempt <= 0:
            raise ValueError("attempt must be > 0")

        self.root = Path(root)
        self.task_id = task_id
        self.scenario_id = scenario_id
        self.attempt = attempt

        self.path = (
            self.root
            / task_id
            / scenario_id
            / f"attempt_{attempt:03d}"
        )

        self.finalized_marker = self.path / "finalized.json"

    @property
    def is_finalized(self) -> bool:
        return self.finalized_marker.exists()

    def create(self) -> Path:
        self.path.mkdir(parents=True, exist_ok=True)

        if not (self.path / "events.jsonl").exists():
            (self.path / "events.jsonl").touch()

        return self.path

    def _ensure_mutable(self) -> None:
        if self.is_finalized:
            raise EvidenceFinalizedError(
                f"Attempt already finalized: {self.path}"
            )

    def write_json(
        self,
        filename: str,
        data: Any,
    ) -> Path:
        self._ensure_mutable()

        p = self.path / filename
        p.parent.mkdir(parents=True, exist_ok=True)

        p.write_text(
            json.dumps(
                data,
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )

        return p

    def append_event(
        self,
        event_type: str,
        data: dict[str, Any] | None = None,
    ) -> None:
        self._ensure_mutable()

        event = {
            "timestamp": datetime.now(
                timezone.utc
            ).isoformat(),
            "type": event_type,
            "data": data or {},
        }

        with (self.path / "events.jsonl").open(
            "a",
            encoding="utf-8",
        ) as f:
            f.write(
                json.dumps(
                    event,
                    sort_keys=True,
                )
                + "\n"
            )

    def write_metadata(
        self,
        extra: dict[str, Any] | None = None,
    ) -> Path:
        metadata = {
            "task_id": self.task_id,
            "scenario_id": self.scenario_id,
            "attempt": self.attempt,
            "created_at": datetime.now(
                timezone.utc
            ).isoformat(),
            "python_version": sys.version.split()[0],
            "platform": platform.platform(),
        }

        if extra:
            metadata.update(extra)

        return self.write_json(
            "metadata.json",
            metadata,
        )

    @staticmethod
    def _sha256(path: Path) -> str:
        h = hashlib.sha256()

        with path.open("rb") as f:
            for chunk in iter(
                lambda: f.read(1024 * 1024),
                b"",
            ):
                h.update(chunk)

        return h.hexdigest()

    def build_manifest(self) -> dict[str, str]:
        manifest: dict[str, str] = {}

        for p in sorted(self.path.rglob("*")):
            if not p.is_file():
                continue

            if p.name == "finalized.json":
                continue

            manifest[
                str(p.relative_to(self.path))
            ] = self._sha256(p)

        return manifest

    def finalize(self) -> Path:
        self._ensure_mutable()

        manifest = self.build_manifest()

        payload = {
            "task_id": self.task_id,
            "scenario_id": self.scenario_id,
            "attempt": self.attempt,
            "finalized_at": datetime.now(
                timezone.utc
            ).isoformat(),
            "files": manifest,
        }

        self.finalized_marker.write_text(
            json.dumps(
                payload,
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )

        # Make evidence files read-only at filesystem level.
        for p in self.path.rglob("*"):
            if p.is_file():
                p.chmod(0o444)

        return self.finalized_marker

    def verify_integrity(self) -> tuple[bool, list[str]]:
        if not self.is_finalized:
            return False, ["attempt_not_finalized"]

        expected = json.loads(
            self.finalized_marker.read_text(
                encoding="utf-8"
            )
        )["files"]

        problems: list[str] = []

        for rel, expected_hash in expected.items():
            p = self.path / rel

            if not p.exists():
                problems.append(
                    f"missing:{rel}"
                )
                continue

            actual = self._sha256(p)

            if actual != expected_hash:
                problems.append(
                    f"hash_mismatch:{rel}"
                )

        current_files = {
            str(p.relative_to(self.path))
            for p in self.path.rglob("*")
            if p.is_file()
            and p.name != "finalized.json"
        }

        unexpected = current_files - set(expected)

        for rel in sorted(unexpected):
            problems.append(
                f"unexpected:{rel}"
            )

        return len(problems) == 0, problems
