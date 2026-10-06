#!/usr/bin/env python3
"""
Aggregate NAV-001 STEP 34 experiment matrix.

Reads existing simulation evidence only. Does not control a vehicle.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
from pathlib import Path

DEFAULT_PROJECT = Path.home() / "UAV" / "st0_project"
DEFAULT_MATRIX_ROOT = Path.home() / "UAV" / "st0_agent_jobs" / "NAV-001" / "step34_matrix"


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def read_first_telemetry(path: Path):
    with path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        row = next(reader)
    return row


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", type=Path, default=DEFAULT_PROJECT)
    ap.add_argument("--matrix-root", type=Path, default=DEFAULT_MATRIX_ROOT)
    args = ap.parse_args()

    project = args.project.expanduser().resolve()
    root = args.matrix_root.expanduser().resolve()
    matrix = load_json(root / "matrix.json")
    ws = root / "workspace"

    rows = []
    missing = []

    for scenario in matrix["scenarios"]:
        sid = scenario["id"]
        scenario_id = f"STEP34-{sid}"
        attempt = ws / "runs/NAV-001" / scenario_id / "attempt_001"

        required = [
            attempt / "input.json",
            attempt / "telemetry.csv",
            attempt / "module_output.json",
            attempt / "verification.json",
        ]
        if not all(p.is_file() and p.stat().st_size > 0 for p in required):
            missing.append(sid)
            continue

        inp = load_json(attempt / "input.json")
        mod = load_json(attempt / "module_output.json")
        ver = load_json(attempt / "verification.json")
        first = read_first_telemetry(attempt / "telemetry.csv")

        sx, sy, sz = float(first["x"]), float(first["y"]), float(first["z"])
        tx, ty, tz = [float(v) for v in inp["target_position_m"]]
        initial_error = math.sqrt(
            (sx - tx) ** 2 + (sy - ty) ** 2 + (sz - tz) ** 2
        )

        clean = (
            math.hypot(sx, sy) <= 2.0
            and abs(sz - (-2.0)) <= 1.0
            and str(first.get("vehicle_state", "")).upper() == "AIRBORNE"
            and str(first.get("localization_valid", "")).lower() == "true"
        )

        rows.append(
            {
                "scenario": sid,
                "target_x": tx,
                "target_y": ty,
                "target_z": tz,
                "initial_error_m": initial_error,
                "final_error_m": float(ver.get("position_error_m")),
                "elapsed_time_s": float(ver.get("elapsed_time_s")),
                "module_status": mod.get("status"),
                "verifier": ver.get("overall"),
                "clean_start": "PASS" if clean else "FAIL",
            }
        )

    if missing:
        print("STEP34_MATRIX=INCOMPLETE")
        print("MISSING=" + ",".join(missing))
        print(f"COMPLETED={len(rows)}/{len(matrix['scenarios'])}")
        return 2

    out_dir = project / "reports/pipeline_v2/step34_matrix"
    out_dir.mkdir(parents=True, exist_ok=True)

    csv_path = out_dir / "matrix_results.csv"
    with csv_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)

    valid = [r for r in rows if r["clean_start"] == "PASS"]
    passed = [r for r in valid if r["verifier"] == "PASS"]

    success_rate = (len(passed) / len(valid) * 100.0) if valid else 0.0
    errors = [r["final_error_m"] for r in valid]
    durations = [r["elapsed_time_s"] for r in valid]

    summary = {
        "task": "NAV-001",
        "step": 34,
        "scope": "PX4_SITL_SIMULATION_ONLY",
        "scenario_count": len(rows),
        "clean_start_count": len(valid),
        "pass_count": len(passed),
        "success_rate_percent": success_rate,
        "mean_final_error_m": statistics.mean(errors) if errors else None,
        "max_final_error_m": max(errors) if errors else None,
        "mean_elapsed_time_s": statistics.mean(durations) if durations else None,
        "max_elapsed_time_s": max(durations) if durations else None,
        "all_clean_start": len(valid) == len(rows),
        "all_verifier_pass": len(passed) == len(rows),
    }

    status = (
        "PASS"
        if summary["all_clean_start"] and summary["all_verifier_pass"]
        else "FAIL"
    )
    summary["overall"] = status

    (out_dir / "matrix_summary.json").write_text(
        json.dumps(summary, indent=2) + "\n",
        encoding="utf-8",
    )

    md = [
        "# NAV-001 STEP 34 — Experiment Matrix",
        "",
        f"Overall: **{status}**",
        "",
        f"- Scenarios: {summary['scenario_count']}",
        f"- Clean-start runs: {summary['clean_start_count']}",
        f"- Verifier PASS: {summary['pass_count']}",
        f"- Success rate: {summary['success_rate_percent']:.1f}%",
        f"- Mean final error: {summary['mean_final_error_m']:.6f} m",
        f"- Max final error: {summary['max_final_error_m']:.6f} m",
        f"- Mean elapsed time: {summary['mean_elapsed_time_s']:.3f} s",
        f"- Max elapsed time: {summary['max_elapsed_time_s']:.3f} s",
        "",
        "| Scenario | Target | Clean start | Verifier | Final error | Elapsed |",
        "|---|---|---|---|---:|---:|",
    ]
    for r in rows:
        md.append(
            f"| {r['scenario']} | "
            f"({r['target_x']:.1f}, {r['target_y']:.1f}, {r['target_z']:.1f}) | "
            f"{r['clean_start']} | {r['verifier']} | "
            f"{r['final_error_m']:.6f} m | {r['elapsed_time_s']:.3f} s |"
        )

    (out_dir / "matrix_report.md").write_text(
        "\n".join(md) + "\n",
        encoding="utf-8",
    )

    print("=== NAV-001 STEP 34 AGGREGATE ===")
    print(f"SCENARIOS={summary['scenario_count']}")
    print(f"CLEAN_START={summary['clean_start_count']}/{summary['scenario_count']}")
    print(f"VERIFIER_PASS={summary['pass_count']}/{summary['scenario_count']}")
    print(f"SUCCESS_RATE={summary['success_rate_percent']:.1f}%")
    print(f"MEAN_ERROR_M={summary['mean_final_error_m']:.6f}")
    print(f"MAX_ERROR_M={summary['max_final_error_m']:.6f}")
    print(f"MEAN_ELAPSED_S={summary['mean_elapsed_time_s']:.3f}")
    print(f"MAX_ELAPSED_S={summary['max_elapsed_time_s']:.3f}")
    print(f"STEP34_MATRIX={status}")
    print(f"REPORT={out_dir / 'matrix_report.md'}")
    return 0 if status == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
