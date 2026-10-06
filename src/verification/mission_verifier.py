from __future__ import annotations

import csv
import hashlib
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from src.runtime_paths import runs_root


@dataclass(frozen=True)
class MissionVerificationResult:
    overall: str
    checks: dict[str, str]
    max_transition_distance_m: float | None
    terminal_home_distance_m: float | None
    failed_step_ids: tuple[str, ...]
    reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "overall": self.overall,
            "checks": dict(self.checks),
            "max_transition_distance_m": self.max_transition_distance_m,
            "terminal_home_distance_m": self.terminal_home_distance_m,
            "failed_step_ids": list(self.failed_step_ids),
            "reason": self.reason,
        }


class MissionVerifier:
    """Independent deterministic verifier for MISSION-001.

    It does not trust mission-runner self assessment. It reads frozen child
    evidence, validates child hashes and authoritative child verification files,
    checks declared/executed order, measures inter-step continuity from telemetry
    and verifies the terminal landed/home state.
    """

    def __init__(self, project: Path) -> None:
        self.project = Path(project).resolve()
        self.runs_root = runs_root(self.project)
        self.spec_path = self.project / "specs" / "MISSION-001.yaml"
        self.spec = self._load_spec()

    def _load_spec(self) -> dict[str, Any]:
        raw = yaml.safe_load(self.spec_path.read_text(encoding="utf-8")) or {}
        if raw.get("spec_id") != "MISSION-001":
            raise ValueError("Unexpected mission specification")
        if raw.get("status") != "frozen":
            raise ValueError("MISSION-001 must be frozen before verification")
        acceptance = raw.get("acceptance") or {}
        if acceptance.get("authoritative_verifier") != "independent_python_mission_verifier":
            raise ValueError("MISSION-001 does not authorize this verifier")
        return raw

    @staticmethod
    def _sha256(path: Path) -> str:
        h = hashlib.sha256()
        with path.open("rb") as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                h.update(chunk)
        return h.hexdigest()

    @classmethod
    def _verify_finalized_attempt(cls, attempt: Path) -> bool:
        finalized = attempt / "finalized.json"
        if not finalized.is_file():
            return False
        try:
            payload = json.loads(finalized.read_text(encoding="utf-8"))
            files = payload["files"]
            if not isinstance(files, dict):
                return False
            for rel, expected in files.items():
                path = attempt / rel
                if not path.is_file() or cls._sha256(path) != expected:
                    return False
            current = {
                str(p.relative_to(attempt))
                for p in attempt.rglob("*")
                if p.is_file() and p.name != "finalized.json"
            }
            return current == set(files)
        except Exception:
            return False

    @staticmethod
    def _telemetry_endpoints(
        path: Path,
    ) -> tuple[
        tuple[float, float, float, str],
        tuple[float, float, float, str],
    ]:
        first: dict[str, str] | None = None
        last: dict[str, str] | None = None
        with path.open("r", encoding="utf-8", newline="") as f:
            for row in csv.DictReader(f):
                if first is None:
                    first = row
                last = row
        if first is None or last is None:
            raise ValueError("telemetry_empty")
        return (
            (
                float(first["x"]),
                float(first["y"]),
                float(first["z"]),
                str(first["vehicle_state"]),
            ),
            (
                float(last["x"]),
                float(last["y"]),
                float(last["z"]),
                str(last["vehicle_state"]),
            ),
        )

    def verify_attempt(
        self,
        attempt_dir: Path,
        *,
        write_result: bool = True,
    ) -> MissionVerificationResult:
        attempt_dir = Path(attempt_dir)
        try:
            plan = json.loads(
                (attempt_dir / "mission_input.json").read_text(encoding="utf-8")
            )
            step_manifest = json.loads(
                (attempt_dir / "mission_steps.json").read_text(encoding="utf-8")
            )
            steps = step_manifest.get("steps") or []
            continuity_tolerance = float(plan["continuity_tolerance_m"])
            home_n, home_e = (float(v) for v in plan["terminal_home_position_m"])
            terminal_home_tolerance = float(plan["terminal_home_tolerance_m"])
        except Exception as exc:
            return self._failure(
                attempt_dir,
                f"invalid_mission_artifacts:{type(exc).__name__}:{exc}",
                write_result,
            )

        declared = tuple(
            str(step.get("capability_id")) for step in (plan.get("steps") or [])
        )
        actual = tuple(str(step.get("capability_id")) for step in steps)
        sequence_ok = actual == declared and len(actual) > 0

        failed_step_ids: list[str] = []
        child_verdicts_ok = len(steps) == len(declared) and bool(steps)
        child_integrity_ok = child_verdicts_ok
        endpoints: list[
            tuple[
                tuple[float, float, float, str],
                tuple[float, float, float, str],
            ]
        ] = []

        for step in steps:
            step_id = str(step.get("step_id") or "unknown")
            rel_value = step.get("attempt_relpath")
            if not rel_value:
                failed_step_ids.append(step_id)
                child_verdicts_ok = False
                child_integrity_ok = False
                continue

            child = self.runs_root / Path(str(rel_value))
            if not child.is_dir() or not self._verify_finalized_attempt(child):
                failed_step_ids.append(step_id)
                child_verdicts_ok = False
                child_integrity_ok = False
                continue

            if str(step.get("finalized_sha256") or "") != self._sha256(
                child / "finalized.json"
            ):
                child_integrity_ok = False

            try:
                child_verification = json.loads(
                    (child / "verification.json").read_text(encoding="utf-8")
                )
                authoritative_overall = str(
                    child_verification.get("overall") or "FAIL"
                )
                if authoritative_overall != "PASS":
                    failed_step_ids.append(step_id)
                    child_verdicts_ok = False
                if str(step.get("overall")) != authoritative_overall:
                    child_integrity_ok = False
                    child_verdicts_ok = False
                endpoints.append(
                    self._telemetry_endpoints(child / "telemetry.csv")
                )
            except Exception:
                failed_step_ids.append(step_id)
                child_verdicts_ok = False
                child_integrity_ok = False

        transition_distances: list[float] = []
        continuity_ok = child_integrity_ok and len(endpoints) == len(steps)
        if continuity_ok:
            for index in range(len(endpoints) - 1):
                previous_end = endpoints[index][1][:3]
                next_start = endpoints[index + 1][0][:3]
                distance = math.dist(previous_end, next_start)
                transition_distances.append(distance)
                if distance > continuity_tolerance:
                    continuity_ok = False

        terminal_land_step_ok = bool(declared) and declared[-1] == "LAND-001"
        terminal_landed_ok = False
        terminal_home_ok = False
        terminal_home_distance: float | None = None
        if endpoints:
            terminal = endpoints[-1][1]
            terminal_landed_ok = terminal[3].strip().upper() != "AIRBORNE"
            terminal_home_distance = math.hypot(
                terminal[0] - home_n,
                terminal[1] - home_e,
            )
            terminal_home_ok = terminal_home_distance <= terminal_home_tolerance

        checks = {
            "step_sequence": "PASS" if sequence_ok else "FAIL",
            "child_verdicts": "PASS" if child_verdicts_ok else "FAIL",
            "child_evidence_integrity": "PASS" if child_integrity_ok else "FAIL",
            "continuity": "PASS" if continuity_ok else "FAIL",
            "terminal_land_step": "PASS" if terminal_land_step_ok else "FAIL",
            "terminal_landed_state": "PASS" if terminal_landed_ok else "FAIL",
            "terminal_home": "PASS" if terminal_home_ok else "FAIL",
        }
        result = MissionVerificationResult(
            overall=(
                "PASS"
                if all(value == "PASS" for value in checks.values())
                else "FAIL"
            ),
            checks=checks,
            max_transition_distance_m=(
                max(transition_distances) if transition_distances else 0.0
            ),
            terminal_home_distance_m=terminal_home_distance,
            failed_step_ids=tuple(dict.fromkeys(failed_step_ids)),
            reason=None,
        )
        if write_result:
            self._write_result(attempt_dir, result)
        return result

    def _failure(
        self,
        attempt_dir: Path,
        reason: str,
        write_result: bool,
    ) -> MissionVerificationResult:
        checks = {
            "step_sequence": "FAIL",
            "child_verdicts": "FAIL",
            "child_evidence_integrity": "FAIL",
            "continuity": "FAIL",
            "terminal_land_step": "FAIL",
            "terminal_landed_state": "FAIL",
            "terminal_home": "FAIL",
        }
        result = MissionVerificationResult(
            overall="FAIL",
            checks=checks,
            max_transition_distance_m=None,
            terminal_home_distance_m=None,
            failed_step_ids=(),
            reason=reason,
        )
        if write_result:
            self._write_result(attempt_dir, result)
        return result

    @staticmethod
    def _write_result(
        attempt_dir: Path,
        result: MissionVerificationResult,
    ) -> None:
        (Path(attempt_dir) / "verification.json").write_text(
            json.dumps(result.to_dict(), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
