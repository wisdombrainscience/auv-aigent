from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path

from src.verification.mission_verifier import MissionVerifier

FIELDS = [
    "timestamp", "x", "y", "z", "vx", "vy", "vz",
    "vehicle_state", "localization_valid",
]


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()


def _child(
    runs_root: Path,
    capability: str,
    scenario: str,
    start: tuple[float, float, float],
    end: tuple[float, float, float],
    *,
    first_state: str = "AIRBORNE",
    final_state: str = "AIRBORNE",
    overall: str = "PASS",
) -> Path:
    attempt = runs_root / capability / scenario / "attempt_001"
    attempt.mkdir(parents=True)
    telemetry = attempt / "telemetry.csv"
    with telemetry.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerow({
            "timestamp": 1.0, "x": start[0], "y": start[1], "z": start[2],
            "vx": 0, "vy": 0, "vz": 0,
            "vehicle_state": first_state, "localization_valid": True,
        })
        writer.writerow({
            "timestamp": 2.0, "x": end[0], "y": end[1], "z": end[2],
            "vx": 0, "vy": 0, "vz": 0,
            "vehicle_state": final_state, "localization_valid": True,
        })
    verification = attempt / "verification.json"
    verification.write_text(json.dumps({"overall": overall}) + "\n", encoding="utf-8")
    files = {
        "telemetry.csv": _sha256(telemetry),
        "verification.json": _sha256(verification),
    }
    (attempt / "finalized.json").write_text(
        json.dumps({"files": files}) + "\n", encoding="utf-8"
    )
    return attempt


def _mission_attempt(
    tmp_path: Path,
    *,
    tamper: bool = False,
    jump: bool = False,
    terminal_xy: tuple[float, float] = (0.1, 0.0),
) -> tuple[Path, Path]:
    runs_root = tmp_path / "runs"
    takeoff = _child(
        runs_root, "TAKEOFF-001", "s1", (0, 0, 0), (0, 0, -3), first_state="DISARMED"
    )
    nav_start = (2.0, 0, -3) if jump else (0.05, 0, -3)
    nav = _child(runs_root, "NAV-001", "s2", nav_start, (5, 0, -3))
    rth = _child(runs_root, "RETURN-HOME-001", "s3", (5.05, 0, -3), (0.2, 0, -3))
    land = _child(
        runs_root,
        "LAND-001",
        "s4",
        (0.2, 0, -3),
        (terminal_xy[0], terminal_xy[1], 0),
        final_state="DISARMED",
    )

    if tamper:
        with (nav / "verification.json").open("a", encoding="utf-8") as f:
            f.write("tampered\n")

    children = [
        ("takeoff", "TAKEOFF-001", takeoff),
        ("outbound", "NAV-001", nav),
        ("return_home", "RETURN-HOME-001", rth),
        ("land", "LAND-001", land),
    ]
    mission = runs_root / "MISSION-001" / "UNIT" / "attempt_001"
    mission.mkdir(parents=True)
    plan = {
        "mission_id": "MISSION-001",
        "frame": "LOCAL_NED",
        "continuity_tolerance_m": 1.0,
        "terminal_home_position_m": [0.0, 0.0],
        "terminal_home_tolerance_m": 0.75,
        "steps": [
            {"step_id": sid, "capability_id": cid, "post_action": "none", "input": {}}
            for sid, cid, _ in children
        ],
    }
    records = []
    for index, (sid, cid, child) in enumerate(children, start=1):
        records.append({
            "sequence": index,
            "step_id": sid,
            "capability_id": cid,
            "overall": "PASS",
            "attempt_relpath": str(child.relative_to(runs_root)),
            "finalized_sha256": _sha256(child / "finalized.json"),
            "verification_checks": {},
        })
    (mission / "mission_input.json").write_text(json.dumps(plan) + "\n", encoding="utf-8")
    (mission / "mission_steps.json").write_text(
        json.dumps({"steps": records}) + "\n", encoding="utf-8"
    )
    return mission, runs_root


def test_mission_verifier_accepts_verified_continuous_home_loop(tmp_path: Path) -> None:
    mission, runs_root = _mission_attempt(tmp_path)
    project = Path(__file__).resolve().parents[2]
    verifier = MissionVerifier(project)
    verifier.runs_root = runs_root
    result = verifier.verify_attempt(mission, write_result=False)
    assert result.overall == "PASS"
    assert all(value == "PASS" for value in result.checks.values())


def test_mission_verifier_rejects_tampered_child_evidence(tmp_path: Path) -> None:
    mission, runs_root = _mission_attempt(tmp_path, tamper=True)
    project = Path(__file__).resolve().parents[2]
    verifier = MissionVerifier(project)
    verifier.runs_root = runs_root
    result = verifier.verify_attempt(mission, write_result=False)
    assert result.overall == "FAIL"
    assert result.checks["child_evidence_integrity"] == "FAIL"


def test_mission_verifier_rejects_inter_step_discontinuity(tmp_path: Path) -> None:
    mission, runs_root = _mission_attempt(tmp_path, jump=True)
    project = Path(__file__).resolve().parents[2]
    verifier = MissionVerifier(project)
    verifier.runs_root = runs_root
    result = verifier.verify_attempt(mission, write_result=False)
    assert result.overall == "FAIL"
    assert result.checks["continuity"] == "FAIL"


def test_mission_verifier_rejects_terminal_position_away_from_home(tmp_path: Path) -> None:
    mission, runs_root = _mission_attempt(tmp_path, terminal_xy=(2.0, 0.0))
    project = Path(__file__).resolve().parents[2]
    verifier = MissionVerifier(project)
    verifier.runs_root = runs_root
    result = verifier.verify_attempt(mission, write_result=False)
    assert result.overall == "FAIL"
    assert result.checks["terminal_home"] == "FAIL"
