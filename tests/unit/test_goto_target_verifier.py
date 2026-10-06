import csv
import json
from pathlib import Path

from src.verification.goto_target_verifier import (
    GoToTargetVerifier,
)


def write_spec(path: Path):
    path.write_text(
        """
spec_id: NAV-001
status: frozen
acceptance:
  authoritative_verifier: independent_python_verifier
""".strip()
        + "\n",
        encoding="utf-8",
    )


def write_input(
    path: Path,
    target=(10.0, 5.0, -3.0),
    tolerance=0.5,
    timeout=30.0,
):
    path.write_text(
        json.dumps(
            {
                "target_position_m": list(target),
                "position_tolerance_m": tolerance,
                "timeout_s": timeout,
            }
        ),
        encoding="utf-8",
    )


def write_telemetry(
    path: Path,
    samples,
):
    fields = [
        "timestamp",
        "x",
        "y",
        "z",
        "vx",
        "vy",
        "vz",
        "vehicle_state",
        "localization_valid",
    ]

    with path.open(
        "w",
        newline="",
        encoding="utf-8",
    ) as f:
        writer = csv.DictWriter(
            f,
            fieldnames=fields,
        )
        writer.writeheader()
        writer.writerows(samples)


def sample(
    t,
    x,
    y,
    z,
    localization=True,
):
    return {
        "timestamp": t,
        "x": x,
        "y": y,
        "z": z,
        "vx": 0.0,
        "vy": 0.0,
        "vz": 0.0,
        "vehicle_state": "AIRBORNE",
        "localization_valid": localization,
    }


def make_verifier(tmp_path):
    spec = tmp_path / "NAV-001.yaml"
    write_spec(spec)
    return GoToTargetVerifier(spec)


def test_known_pass_case(tmp_path):
    attempt = tmp_path / "attempt_001"
    attempt.mkdir()

    write_input(attempt / "input.json")

    write_telemetry(
        attempt / "telemetry.csv",
        [
            sample(
                100.0,
                0.0,
                0.0,
                -2.0,
            ),
            sample(
                110.0,
                10.12,
                4.91,
                -3.06,
            ),
        ],
    )

    result = make_verifier(
        tmp_path
    ).verify_attempt(attempt)

    assert result.overall == "PASS"
    assert result.checks["position"] == "PASS"
    assert result.checks["timeout"] == "PASS"
    assert result.checks["telemetry"] == "PASS"


def test_known_fail_position(tmp_path):
    attempt = tmp_path / "attempt_001"
    attempt.mkdir()

    write_input(attempt / "input.json")

    write_telemetry(
        attempt / "telemetry.csv",
        [
            sample(
                100.0,
                0.0,
                0.0,
                -2.0,
            ),
            sample(
                110.0,
                10.0,
                5.0,
                -5.0,
            ),
        ],
    )

    result = make_verifier(
        tmp_path
    ).verify_attempt(attempt)

    assert result.overall == "FAIL"
    assert result.checks["position"] == "FAIL"


def test_timeout_failure(tmp_path):
    attempt = tmp_path / "attempt_001"
    attempt.mkdir()

    write_input(
        attempt / "input.json",
        timeout=5.0,
    )

    write_telemetry(
        attempt / "telemetry.csv",
        [
            sample(
                100.0,
                10.0,
                5.0,
                -3.0,
            ),
            sample(
                110.0,
                10.0,
                5.0,
                -3.0,
            ),
        ],
    )

    result = make_verifier(
        tmp_path
    ).verify_attempt(attempt)

    assert result.overall == "FAIL"
    assert result.checks["timeout"] == "FAIL"


def test_invalid_localization_fails(tmp_path):
    attempt = tmp_path / "attempt_001"
    attempt.mkdir()

    write_input(attempt / "input.json")

    write_telemetry(
        attempt / "telemetry.csv",
        [
            sample(
                100.0,
                10.0,
                5.0,
                -3.0,
                localization=True,
            ),
            sample(
                101.0,
                10.0,
                5.0,
                -3.0,
                localization=False,
            ),
        ],
    )

    result = make_verifier(
        tmp_path
    ).verify_attempt(attempt)

    assert result.overall == "FAIL"
    assert result.checks["telemetry"] == "FAIL"


def test_module_status_is_ignored(tmp_path):
    attempt = tmp_path / "attempt_001"
    attempt.mkdir()

    write_input(attempt / "input.json")

    write_telemetry(
        attempt / "telemetry.csv",
        [
            sample(
                100.0,
                0.0,
                0.0,
                0.0,
            ),
            sample(
                101.0,
                10.0,
                5.0,
                -3.0,
            ),
        ],
    )

    (
        attempt
        / "module_output.json"
    ).write_text(
        json.dumps(
            {
                "status": "FAILED",
                "claim": "I failed",
            }
        ),
        encoding="utf-8",
    )

    result = make_verifier(
        tmp_path
    ).verify_attempt(attempt)

    assert result.overall == "PASS"
