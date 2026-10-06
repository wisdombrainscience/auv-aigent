from __future__ import annotations

import csv
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class VerificationResult:
    task: str
    position_error_m: float | None
    elapsed_time_s: float | None
    checks: dict[str, str]
    overall: str
    reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "task": self.task,
            "position_error_m": self.position_error_m,
            "elapsed_time_s": self.elapsed_time_s,
            "checks": self.checks,
            "overall": self.overall,
            "reason": self.reason,
        }


class GoToTargetVerifier:
    """Authoritative NAV-001 verifier using bounded-memory telemetry streaming."""

    REQUIRED_TELEMETRY_FIELDS = {
        "timestamp",
        "x",
        "y",
        "z",
        "vx",
        "vy",
        "vz",
        "vehicle_state",
        "localization_valid",
    }

    def __init__(self, spec_path: Path) -> None:
        self.spec_path = Path(spec_path)
        self.spec = self._load_spec()

    def _load_spec(self) -> dict[str, Any]:
        with self.spec_path.open("r", encoding="utf-8") as f:
            spec = yaml.safe_load(f)
        if spec.get("spec_id") != "NAV-001":
            raise ValueError("Unexpected specification")
        if spec.get("status") != "frozen":
            raise ValueError("NAV-001 must be frozen before verification")
        acceptance = spec.get("acceptance", {})
        if acceptance.get("authoritative_verifier") != "independent_python_verifier":
            raise ValueError("NAV-001 does not authorize this verifier")
        return spec

    @staticmethod
    def _load_json(path: Path) -> dict[str, Any]:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)

    @staticmethod
    def _parse_bool(value: str) -> bool:
        return value.strip().lower() in {"true", "1", "yes"}

    @staticmethod
    def _finite_float(value: str) -> float:
        number = float(value)
        if not math.isfinite(number):
            raise ValueError(f"Non-finite telemetry value: {value}")
        return number

    def verify_attempt(self, attempt_dir: Path, write_result: bool = True) -> VerificationResult:
        attempt_dir = Path(attempt_dir)
        input_path = attempt_dir / "input.json"
        telemetry_path = attempt_dir / "telemetry.csv"
        task_name = attempt_dir.name

        if not input_path.exists():
            return self._finish_failure(attempt_dir, task_name, "missing_input_json", write_result)
        if not telemetry_path.exists():
            return self._finish_failure(attempt_dir, task_name, "missing_telemetry_csv", write_result)

        try:
            input_data = self._load_json(input_path)
            target = input_data["target_position_m"]
            tolerance = float(input_data["position_tolerance_m"])
            timeout = float(input_data["timeout_s"])
            if len(target) != 3:
                raise ValueError("target_position_m must contain 3 values")
            tx, ty, tz = map(float, target)
            for value in (tx, ty, tz, tolerance, timeout):
                if not math.isfinite(value):
                    raise ValueError("Input contains non-finite value")
            if tolerance <= 0:
                raise ValueError("Tolerance must be > 0")
            if timeout <= 0:
                raise ValueError("Timeout must be > 0")
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            return self._finish_failure(attempt_dir, task_name, f"invalid_input:{exc}", write_result)

        try:
            first_timestamp: float | None = None
            previous_timestamp: float | None = None
            final: tuple[float, float, float] | None = None
            telemetry_valid = True
            row_count = 0

            with telemetry_path.open("r", newline="", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                fields = set(reader.fieldnames or [])
                missing = self.REQUIRED_TELEMETRY_FIELDS - fields
                if missing:
                    raise ValueError("missing_fields:" + ",".join(sorted(missing)))

                for row in reader:
                    timestamp = self._finite_float(row["timestamp"])
                    x = self._finite_float(row["x"])
                    y = self._finite_float(row["y"])
                    z = self._finite_float(row["z"])
                    if previous_timestamp is not None and timestamp < previous_timestamp:
                        raise ValueError("timestamps_not_monotonic")
                    if first_timestamp is None:
                        first_timestamp = timestamp
                    previous_timestamp = timestamp
                    final = (x, y, z)
                    telemetry_valid = telemetry_valid and self._parse_bool(row["localization_valid"])
                    row_count += 1

            if row_count == 0 or first_timestamp is None or previous_timestamp is None or final is None:
                raise ValueError("telemetry_empty")

            elapsed = previous_timestamp - first_timestamp
            dx = final[0] - tx
            dy = final[1] - ty
            dz = final[2] - tz
            position_error = math.sqrt(dx * dx + dy * dy + dz * dz)
        except (ValueError, TypeError, csv.Error) as exc:
            return self._finish_failure(attempt_dir, task_name, f"invalid_telemetry:{exc}", write_result)

        checks = {
            "position": "PASS" if position_error <= tolerance else "FAIL",
            "timeout": "PASS" if elapsed <= timeout else "FAIL",
            "telemetry": "PASS" if telemetry_valid else "FAIL",
        }
        result = VerificationResult(
            task=task_name,
            position_error_m=position_error,
            elapsed_time_s=elapsed,
            checks=checks,
            overall="PASS" if all(value == "PASS" for value in checks.values()) else "FAIL",
        )
        if write_result:
            self._write_result(attempt_dir, result)
        return result

    def _finish_failure(
        self,
        attempt_dir: Path,
        task: str,
        reason: str,
        write_result: bool,
    ) -> VerificationResult:
        result = self._fail(task, reason)
        if write_result:
            self._write_result(attempt_dir, result)
        return result

    def _fail(self, task: str, reason: str) -> VerificationResult:
        return VerificationResult(
            task=task,
            position_error_m=None,
            elapsed_time_s=None,
            checks={"position": "FAIL", "timeout": "FAIL", "telemetry": "FAIL"},
            overall="FAIL",
            reason=reason,
        )

    @staticmethod
    def _write_result(attempt_dir: Path, result: VerificationResult) -> None:
        path = attempt_dir / "verification.json"
        path.write_text(
            json.dumps(result.to_dict(), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
