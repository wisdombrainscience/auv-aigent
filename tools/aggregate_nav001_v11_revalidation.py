#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import statistics
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_PROJECT = Path.home() / "UAV" / "st0_project"
DEFAULT_ROOT = Path.home() / "UAV" / "st0_agent_jobs" / "NAV-001" / "v1_1_revalidation"


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def first_row(path: Path):
    with path.open("r", encoding="utf-8", newline="") as f:
        return next(csv.DictReader(f))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", type=Path, default=DEFAULT_PROJECT)
    ap.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    args = ap.parse_args()

    project = args.project.expanduser().resolve()
    root = args.root.expanduser().resolve()
    manifest = load_json(root / "revalidation_manifest.json")
    matrix = load_json(root / "matrix.json")
    ws = Path(manifest["workspace"])

    source = ws / "src/capabilities/navigation/go_to_target.py"
    if sha256(source) != manifest["working_source_sha256"]:
        raise SystemExit("FAIL: frozen workspace source hash changed")

    rows = []
    missing = []

    for scenario in matrix["scenarios"]:
        sid = scenario["id"]
        scenario_id = f"V11-{sid}"
        attempt = ws / "runs/NAV-001" / scenario_id / "attempt_001"

        paths = {
            "input": attempt / "input.json",
            "telemetry": attempt / "telemetry.csv",
            "module": attempt / "module_output.json",
            "verification": attempt / "verification.json",
            "finalized": attempt / "finalized.json",
        }
        if not all(p.is_file() and p.stat().st_size > 0 for p in paths.values()):
            missing.append(sid)
            continue

        inp = load_json(paths["input"])
        module = load_json(paths["module"])
        ver = load_json(paths["verification"])
        first = first_row(paths["telemetry"])

        sx, sy, sz = float(first["x"]), float(first["y"]), float(first["z"])
        clean_start = (
            math.hypot(sx, sy) <= 2.0
            and abs(sz - (-2.0)) <= 1.0
            and str(first.get("vehicle_state", "")).upper() == "AIRBORNE"
            and str(first.get("localization_valid", "")).lower() == "true"
        )

        data = module.get("data") or {}
        required_data = {
            "task_id",
            "status",
            "target_position",
            "final_position",
            "position_error_m",
            "elapsed_time_s",
        }
        contract_shape = required_data.issubset(data.keys())
        final_position = data.get("final_position")
        contract_shape = (
            contract_shape
            and isinstance(data.get("target_position"), list)
            and len(data["target_position"]) == 3
            and isinstance(final_position, list)
            and len(final_position) == 3
        )

        rows.append({
            "scenario": sid,
            "clean_start": "PASS" if clean_start else "FAIL",
            "module_status": module.get("status"),
            "verifier": ver.get("overall"),
            "contract_shape": "PASS" if contract_shape else "FAIL",
            "final_error_m": ver.get("position_error_m"),
            "elapsed_time_s": ver.get("elapsed_time_s"),
        })

    if missing:
        print("V11_REVALIDATION=INCOMPLETE")
        print("MISSING=" + ",".join(missing))
        print(f"COMPLETED={len(rows)}/{len(matrix['scenarios'])}")
        return 2

    pass_rows = [
        r for r in rows
        if r["clean_start"] == "PASS"
        and r["module_status"] == "completed"
        and r["verifier"] == "PASS"
        and r["contract_shape"] == "PASS"
    ]
    overall = "PASS" if len(pass_rows) == len(rows) else "FAIL"

    errors = [float(r["final_error_m"]) for r in rows]
    durations = [float(r["elapsed_time_s"]) for r in rows]

    summary = {
        "task": "NAV-001",
        "version": "v1.1",
        "scope": "PX4_SITL_SIMULATION_ONLY",
        "source_sha256": manifest["working_source_sha256"],
        "scenario_count": len(rows),
        "pass_count": len(pass_rows),
        "all_clean_start": all(r["clean_start"] == "PASS" for r in rows),
        "all_verifier_pass": all(r["verifier"] == "PASS" for r in rows),
        "all_contract_shape_pass": all(r["contract_shape"] == "PASS" for r in rows),
        "mean_final_error_m": statistics.mean(errors),
        "max_final_error_m": max(errors),
        "mean_elapsed_time_s": statistics.mean(durations),
        "max_elapsed_time_s": max(durations),
        "overall": overall,
        "completed_at_utc": datetime.now(timezone.utc).isoformat(),
    }

    out = project / "reports/hardening_v1_1/sitl_revalidation"
    out.mkdir(parents=True, exist_ok=True)

    (out / "summary.json").write_text(
        json.dumps(summary, indent=2) + "\n",
        encoding="utf-8",
    )

    with (out / "results.csv").open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)

    md = [
        "# NAV-001 v1.1 — Clean PX4 SITL Revalidation",
        "",
        f"Overall: **{overall}**",
        "",
        f"- Source SHA256: `{summary['source_sha256']}`",
        f"- Scenarios: {summary['scenario_count']}",
        f"- PASS: {summary['pass_count']}/{summary['scenario_count']}",
        f"- All clean start: {summary['all_clean_start']}",
        f"- All verifier PASS: {summary['all_verifier_pass']}",
        f"- All output-contract shape PASS: {summary['all_contract_shape_pass']}",
        f"- Mean final error: {summary['mean_final_error_m']:.6f} m",
        f"- Max final error: {summary['max_final_error_m']:.6f} m",
        f"- Mean elapsed: {summary['mean_elapsed_time_s']:.3f} s",
        f"- Max elapsed: {summary['max_elapsed_time_s']:.3f} s",
        "",
        "| Scenario | Clean start | Module | Verifier | Contract | Error | Elapsed |",
        "|---|---|---|---|---|---:|---:|",
    ]
    for r in rows:
        md.append(
            f"| {r['scenario']} | {r['clean_start']} | {r['module_status']} | "
            f"{r['verifier']} | {r['contract_shape']} | "
            f"{float(r['final_error_m']):.6f} m | {float(r['elapsed_time_s']):.3f} s |"
        )
    (out / "report.md").write_text("\n".join(md) + "\n", encoding="utf-8")

    print("=== NAV-001 V1.1 SITL REVALIDATION ===")
    print(f"SOURCE_SHA256={summary['source_sha256']}")
    print(f"SCENARIOS={summary['scenario_count']}")
    print(f"PASS={summary['pass_count']}/{summary['scenario_count']}")
    print(f"CLEAN_START={'PASS' if summary['all_clean_start'] else 'FAIL'}")
    print(f"VERIFIER={'PASS' if summary['all_verifier_pass'] else 'FAIL'}")
    print(f"CONTRACT_SHAPE={'PASS' if summary['all_contract_shape_pass'] else 'FAIL'}")
    print(f"MEAN_ERROR_M={summary['mean_final_error_m']:.6f}")
    print(f"MAX_ERROR_M={summary['max_final_error_m']:.6f}")
    print(f"OVERALL={overall}")
    print(f"REPORT={out / 'report.md'}")
    return 0 if overall == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
