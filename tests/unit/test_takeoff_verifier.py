from __future__ import annotations

import csv
import json
from pathlib import Path

from src.verification.takeoff_verifier import TakeoffVerifier


FIELDS = [
    "timestamp", "x", "y", "z", "vx", "vy", "vz",
    "vehicle_state", "localization_valid",
]


def write_attempt(
    root: Path,
    *,
    final_z: float = -3.05,
    final_x: float = 0.1,
    final_y: float = 0.1,
    include_ground: bool = True,
) -> Path:
    attempt = root / "attempt_001"
    attempt.mkdir()
    (attempt / "input.json").write_text(
        json.dumps(
            {
                "task_id": "TAKEOFF-001",
                "frame": "LOCAL_NED",
                "target_altitude_m": 3.0,
                "altitude_tolerance_m": 0.25,
                "horizontal_tolerance_m": 0.5,
                "timeout_s": 10.0,
            }
        ),
        encoding="utf-8",
    )
    rows = []
    if include_ground:
        rows.append(
            {
                "timestamp": 100.0,
                "x": 0.0,
                "y": 0.0,
                "z": 0.0,
                "vx": 0.0,
                "vy": 0.0,
                "vz": 0.0,
                "vehicle_state": "DISARMED",
                "localization_valid": True,
            }
        )
    rows.append(
        {
            "timestamp": 104.0,
            "x": final_x,
            "y": final_y,
            "z": final_z,
            "vx": 0.0,
            "vy": 0.0,
            "vz": 0.0,
            "vehicle_state": "AIRBORNE",
            "localization_valid": True,
        }
    )
    with (attempt / "telemetry.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    return attempt


def verifier(project: Path) -> TakeoffVerifier:
    spec = Path(__file__).resolve().parents[2] / "specs" / "TAKEOFF-001.yaml"
    return TakeoffVerifier(spec)


def test_takeoff_verifier_passes_measured_takeoff(tmp_path: Path) -> None:
    attempt = write_attempt(tmp_path)
    result = verifier(tmp_path).verify_attempt(attempt, write_result=False)
    assert result.overall == "PASS"
    assert result.altitude_error_m is not None and result.altitude_error_m < 0.1
    assert result.horizontal_drift_m is not None and result.horizontal_drift_m < 0.2
    assert all(v == "PASS" for v in result.checks.values())


def test_takeoff_verifier_fails_horizontal_drift(tmp_path: Path) -> None:
    attempt = write_attempt(tmp_path, final_x=1.0, final_y=0.0)
    result = verifier(tmp_path).verify_attempt(attempt, write_result=False)
    assert result.overall == "FAIL"
    assert result.checks["horizontal_drift"] == "FAIL"


def test_takeoff_verifier_requires_observed_takeoff_transition(tmp_path: Path) -> None:
    attempt = write_attempt(tmp_path, include_ground=False)
    result = verifier(tmp_path).verify_attempt(attempt, write_result=False)
    assert result.overall == "FAIL"
    assert result.checks["takeoff_transition"] == "FAIL"
