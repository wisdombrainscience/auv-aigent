from __future__ import annotations

import csv
import json
from pathlib import Path

from src.verification.land_verifier import LandVerifier


FIELDS = [
    "timestamp", "x", "y", "z", "vx", "vy", "vz",
    "vehicle_state", "localization_valid",
]


def write_attempt(
    root: Path,
    *,
    final_z: float = 0.1,
    final_x: float = 0.2,
    final_y: float = 0.1,
    include_airborne: bool = True,
    final_state: str = "DISARMED",
) -> Path:
    attempt = root / "attempt_001"
    attempt.mkdir()
    (attempt / "input.json").write_text(
        json.dumps(
            {
                "task_id": "LAND-001",
                "frame": "LOCAL_NED",
                "ground_altitude_tolerance_m": 0.3,
                "horizontal_tolerance_m": 0.5,
                "timeout_s": 10.0,
            }
        ),
        encoding="utf-8",
    )
    rows = []
    if include_airborne:
        rows.append(
            {
                "timestamp": 100.0,
                "x": 0.0,
                "y": 0.0,
                "z": -3.0,
                "vx": 0.0,
                "vy": 0.0,
                "vz": 0.0,
                "vehicle_state": "AIRBORNE",
                "localization_valid": True,
            }
        )
    rows.append(
        {
            "timestamp": 105.0,
            "x": final_x,
            "y": final_y,
            "z": final_z,
            "vx": 0.0,
            "vy": 0.0,
            "vz": 0.0,
            "vehicle_state": final_state,
            "localization_valid": True,
        }
    )
    with (attempt / "telemetry.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    return attempt


def verifier() -> LandVerifier:
    spec = Path(__file__).resolve().parents[2] / "specs" / "LAND-001.yaml"
    return LandVerifier(spec)


def test_land_verifier_passes_measured_landing(tmp_path: Path) -> None:
    attempt = write_attempt(tmp_path)
    result = verifier().verify_attempt(attempt, write_result=False)
    assert result.overall == "PASS"
    assert result.final_altitude_m is not None
    assert abs(result.final_altitude_m) <= 0.3
    assert result.horizontal_drift_m is not None and result.horizontal_drift_m < 0.3
    assert all(v == "PASS" for v in result.checks.values())


def test_land_verifier_fails_ground_altitude_outside_tolerance(tmp_path: Path) -> None:
    attempt = write_attempt(tmp_path, final_z=-0.8)
    result = verifier().verify_attempt(attempt, write_result=False)
    assert result.overall == "FAIL"
    assert result.checks["ground_altitude"] == "FAIL"


def test_land_verifier_requires_observed_airborne_to_landed_transition(
    tmp_path: Path,
) -> None:
    attempt = write_attempt(tmp_path, include_airborne=False)
    result = verifier().verify_attempt(attempt, write_result=False)
    assert result.overall == "FAIL"
    assert result.checks["landing_transition"] == "FAIL"
