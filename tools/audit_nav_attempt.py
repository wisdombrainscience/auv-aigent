#!/usr/bin/env python3
"""
Audit one NAV-001 attempt for clean-start experiment evidence.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("attempt_dir", type=Path)
    ap.add_argument("--max-origin-xy", type=float, default=2.0)
    ap.add_argument("--expected-start-z", type=float, default=-2.0)
    ap.add_argument("--start-z-tolerance", type=float, default=1.0)
    args = ap.parse_args()

    attempt = args.attempt_dir.expanduser().resolve()
    input_path = attempt / "input.json"
    telemetry_path = attempt / "telemetry.csv"
    verification_path = attempt / "verification.json"
    module_path = attempt / "module_output.json"

    for p in (input_path, telemetry_path, verification_path, module_path):
        if not p.is_file() or p.stat().st_size == 0:
            raise SystemExit(f"FAIL: missing or empty: {p}")

    inp = load_json(input_path)
    ver = load_json(verification_path)
    module = load_json(module_path)

    with telemetry_path.open("r", encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))

    if len(rows) < 2:
        raise SystemExit("FAIL: telemetry has fewer than 2 rows")

    first = rows[0]
    last = rows[-1]
    start = tuple(float(first[k]) for k in ("x", "y", "z"))
    end = tuple(float(last[k]) for k in ("x", "y", "z"))
    target = tuple(float(x) for x in inp["target_position_m"])

    start_error = math.dist(start, target)
    end_error = math.dist(end, target)
    origin_xy = math.hypot(start[0], start[1])

    clean_origin = origin_xy <= args.max_origin_xy
    clean_z = abs(start[2] - args.expected_start_z) <= args.start_z_tolerance
    airborne = str(first.get("vehicle_state", "")).upper() == "AIRBORNE"
    localization = str(first.get("localization_valid", "")).lower() == "true"

    result = {
        "attempt": str(attempt),
        "start_position": start,
        "end_position": end,
        "target": target,
        "initial_distance_to_target_m": start_error,
        "final_distance_to_target_m_from_telemetry": end_error,
        "start_horizontal_distance_from_origin_m": origin_xy,
        "start_near_origin": clean_origin,
        "start_altitude_fixture_match": clean_z,
        "start_airborne": airborne,
        "start_localization_valid": localization,
        "module_status": module.get("status"),
        "verifier_overall": ver.get("overall"),
    }

    clean = clean_origin and clean_z and airborne and localization
    result["clean_start_gate"] = "PASS" if clean else "FAIL"

    print(json.dumps(result, indent=2, ensure_ascii=False))
    print(
        "CLEAN_START_GATE="
        + ("PASS" if clean else "FAIL")
    )
    return 0 if clean else 1


if __name__ == "__main__":
    raise SystemExit(main())
