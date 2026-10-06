from __future__ import annotations

import csv
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class ReturnHomeVerificationResult:
    task: str
    initial_home_distance_m: float | None
    final_home_distance_m: float | None
    altitude_error_m: float | None
    elapsed_time_s: float | None
    checks: dict[str, str]
    overall: str
    reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "task": self.task,
            "initial_home_distance_m": self.initial_home_distance_m,
            "final_home_distance_m": self.final_home_distance_m,
            "altitude_error_m": self.altitude_error_m,
            "elapsed_time_s": self.elapsed_time_s,
            "checks": self.checks,
            "overall": self.overall,
            "reason": self.reason,
        }


class ReturnHomeVerifier:
    """Authoritative RETURN-HOME-001 verifier using frozen telemetry only."""

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
            spec = yaml.safe_load(f) or {}
        if spec.get("spec_id") != "RETURN-HOME-001":
            raise ValueError("Unexpected specification")
        if spec.get("status") != "frozen":
            raise ValueError("RETURN-HOME-001 must be frozen before verification")
        acceptance = spec.get("acceptance", {}) or {}
        if acceptance.get("authoritative_verifier") != "independent_python_verifier":
            raise ValueError("RETURN-HOME-001 does not authorize this verifier")
        return spec

    @staticmethod
    def _load_json(path: Path) -> dict[str, Any]:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)

    @staticmethod
    def _parse_bool(value: str) -> bool:
        return value.strip().lower() in {"true", "1", "yes"}

    @staticmethod
    def _finite_float(value: str | float | int) -> float:
        number = float(value)
        if not math.isfinite(number):
            raise ValueError(f"Non-finite value: {value}")
        return number

    def verify_attempt(
        self,
        attempt_dir: Path,
        write_result: bool = True,
    ) -> ReturnHomeVerificationResult:
        attempt_dir = Path(attempt_dir)
        task_name = attempt_dir.name
        input_path = attempt_dir / "input.json"
        telemetry_path = attempt_dir / "telemetry.csv"

        if not input_path.exists():
            return self._finish_failure(
                attempt_dir, task_name, "missing_input_json", write_result
            )
        if not telemetry_path.exists():
            return self._finish_failure(
                attempt_dir, task_name, "missing_telemetry_csv", write_result
            )

        try:
            input_data = self._load_json(input_path)
            home = input_data["home_position_m"]
            if not isinstance(home, (list, tuple)) or len(home) != 2:
                raise ValueError("home_position_m must contain exactly 2 values")
            home_n = self._finite_float(home[0])
            home_e = self._finite_float(home[1])
            return_altitude = self._finite_float(input_data["return_altitude_m"])
            horizontal_tolerance = self._finite_float(
                input_data["horizontal_tolerance_m"]
            )
            altitude_tolerance = self._finite_float(
                input_data["altitude_tolerance_m"]
            )
            timeout = self._finite_float(input_data["timeout_s"])
            if input_data.get("frame") != "LOCAL_NED":
                raise ValueError("frame must be LOCAL_NED")
            if min(
                return_altitude,
                horizontal_tolerance,
                altitude_tolerance,
                timeout,
            ) <= 0:
                raise ValueError("return-home positive inputs must be > 0")
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            return self._finish_failure(
                attempt_dir, task_name, f"invalid_input:{exc}", write_result
            )

        try:
            first_timestamp: float | None = None
            previous_timestamp: float | None = None
            first_position: tuple[float, float, float] | None = None
            final_position: tuple[float, float, float] | None = None
            final_vehicle_state: str | None = None
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
                    vehicle_state = row["vehicle_state"].strip().upper()

                    if previous_timestamp is not None and timestamp < previous_timestamp:
                        raise ValueError("timestamps_not_monotonic")
                    if first_timestamp is None:
                        first_timestamp = timestamp
                        first_position = (x, y, z)
                    previous_timestamp = timestamp
                    final_position = (x, y, z)
                    final_vehicle_state = vehicle_state
                    telemetry_valid = telemetry_valid and self._parse_bool(
                        row["localization_valid"]
                    )
                    row_count += 1

            if (
                row_count == 0
                or first_timestamp is None
                or previous_timestamp is None
                or first_position is None
                or final_position is None
                or final_vehicle_state is None
            ):
                raise ValueError("telemetry_empty")

            elapsed = previous_timestamp - first_timestamp
            initial_home_distance = math.hypot(
                first_position[0] - home_n,
                first_position[1] - home_e,
            )
            final_home_distance = math.hypot(
                final_position[0] - home_n,
                final_position[1] - home_e,
            )
            final_altitude = -final_position[2]
            altitude_error = abs(final_altitude - return_altitude)
        except (ValueError, TypeError, csv.Error) as exc:
            return self._finish_failure(
                attempt_dir,
                task_name,
                f"invalid_telemetry:{exc}",
                write_result,
            )

        checks = {
            "home_horizontal": (
                "PASS" if final_home_distance <= horizontal_tolerance else "FAIL"
            ),
            "return_altitude": (
                "PASS" if altitude_error <= altitude_tolerance else "FAIL"
            ),
            "airborne": "PASS" if final_vehicle_state == "AIRBORNE" else "FAIL",
            "return_transition": (
                "PASS"
                if (
                    initial_home_distance > horizontal_tolerance
                    and final_home_distance <= horizontal_tolerance
                )
                else "FAIL"
            ),
            "timeout": "PASS" if elapsed <= timeout else "FAIL",
            "telemetry": "PASS" if telemetry_valid else "FAIL",
        }
        result = ReturnHomeVerificationResult(
            task=task_name,
            initial_home_distance_m=initial_home_distance,
            final_home_distance_m=final_home_distance,
            altitude_error_m=altitude_error,
            elapsed_time_s=elapsed,
            checks=checks,
            overall=(
                "PASS" if all(value == "PASS" for value in checks.values()) else "FAIL"
            ),
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
    ) -> ReturnHomeVerificationResult:
        result = ReturnHomeVerificationResult(
            task=task,
            initial_home_distance_m=None,
            final_home_distance_m=None,
            altitude_error_m=None,
            elapsed_time_s=None,
            checks={
                "home_horizontal": "FAIL",
                "return_altitude": "FAIL",
                "airborne": "FAIL",
                "return_transition": "FAIL",
                "timeout": "FAIL",
                "telemetry": "FAIL",
            },
            overall="FAIL",
            reason=reason,
        )
        if write_result:
            self._write_result(attempt_dir, result)
        return result

    @staticmethod
    def _write_result(
        attempt_dir: Path,
        result: ReturnHomeVerificationResult,
    ) -> None:
        (Path(attempt_dir) / "verification.json").write_text(
            json.dumps(result.to_dict(), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
