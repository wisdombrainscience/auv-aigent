from __future__ import annotations

import csv
import json
from pathlib import Path

from src.verification.return_home_verifier import ReturnHomeVerifier


FIELDS = [
    "timestamp", "x", "y", "z", "vx", "vy", "vz",
    "vehicle_state", "localization_valid",
]


def write_attempt(
    root: Path,
    *,
    start_x: float = 5.0,
    start_y: float = 0.0,
    final_x: float = 0.1,
    final_y: float = -0.1,
    final_z: float = -3.1,
    final_state: str = "AIRBORNE",
) -> Path:
    attempt = root / "attempt_001"
    attempt.mkdir()
    (attempt / "input.json").write_text(
        json.dumps(
            {
                "task_id": "RETURN-HOME-001",
                "frame": "LOCAL_NED",
                "home_position_m": [0.0, 0.0],
                "return_altitude_m": 3.0,
                "horizontal_tolerance_m": 0.5,
                "altitude_tolerance_m": 0.5,
                "timeout_s": 10.0,
            }
        ),
        encoding="utf-8",
    )
    rows = [
        {
            "timestamp": 100.0,
            "x": start_x,
            "y": start_y,
            "z": -3.0,
            "vx": 0.0,
            "vy": 0.0,
            "vz": 0.0,
            "vehicle_state": "AIRBORNE",
            "localization_valid": "true",
        },
        {
            "timestamp": 104.0,
            "x": final_x,
            "y": final_y,
            "z": final_z,
            "vx": 0.0,
            "vy": 0.0,
            "vz": 0.0,
            "vehicle_state": final_state,
            "localization_valid": "true",
        },
    ]
    with (attempt / "telemetry.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    return attempt


def test_return_home_verifier_passes_measured_return(tmp_path: Path) -> None:
    project = Path(__file__).resolve().parents[2]
    attempt = write_attempt(tmp_path)
    result = ReturnHomeVerifier(project / "specs/RETURN-HOME-001.yaml").verify_attempt(
        attempt
    )

    assert result.overall == "PASS"
    assert all(value == "PASS" for value in result.checks.values())
    assert result.initial_home_distance_m == 5.0
    assert result.final_home_distance_m is not None
    assert result.final_home_distance_m < 0.2
    assert result.altitude_error_m is not None
    assert result.altitude_error_m < 0.2


def test_return_home_verifier_rejects_no_return_transition(tmp_path: Path) -> None:
    project = Path(__file__).resolve().parents[2]
    attempt = write_attempt(
        tmp_path,
        start_x=0.1,
        start_y=0.1,
        final_x=0.05,
        final_y=0.05,
    )
    result = ReturnHomeVerifier(project / "specs/RETURN-HOME-001.yaml").verify_attempt(
        attempt
    )

    assert result.overall == "FAIL"
    assert result.checks["home_horizontal"] == "PASS"
    assert result.checks["return_transition"] == "FAIL"


def test_return_home_verifier_rejects_landed_terminal_state(tmp_path: Path) -> None:
    project = Path(__file__).resolve().parents[2]
    attempt = write_attempt(tmp_path, final_state="DISARMED")
    result = ReturnHomeVerifier(project / "specs/RETURN-HOME-001.yaml").verify_attempt(
        attempt
    )

    assert result.overall == "FAIL"
    assert result.checks["airborne"] == "FAIL"
