#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import json
import traceback
from pathlib import Path

from evaluation.common import (
    PROJECT, ensure_dir, environment_manifest, extract_attempt_row, source_manifest,
    stamp, write_csv, write_json,
)
from evaluation.direct_runtime import wait_flight_ready
from orchestration.mission_experiment import MissionExperimentRunner
from orchestration.step37a_simulation import SimulationSupervisor
from src.adapters.px4_sitl_adapter import PX4SITLAdapter


def mission(target: list[float]) -> dict:
    return {
        "mission_id": "MISSION-001",
        "frame": "LOCAL_NED",
        "continuity_tolerance_m": 1.0,
        "terminal_home_position_m": [0.0, 0.0],
        "terminal_home_tolerance_m": 1.0,
        "steps": [
            {"step_id": "takeoff", "capability_id": "TAKEOFF-001", "post_action": "hold",
             "input": {"task_id":"TAKEOFF-001","frame":"LOCAL_NED","target_altitude_m":3.0,"altitude_tolerance_m":0.5,"horizontal_tolerance_m":1.0,"timeout_s":30.0}},
            {"step_id": "outbound", "capability_id": "NAV-001", "post_action": "hold",
             "input": {"task_id":"NAV-001","frame":"LOCAL_NED","target_position_m":target,"position_tolerance_m":0.5,"timeout_s":30.0}},
            {"step_id": "return_home", "capability_id": "RETURN-HOME-001", "post_action": "hold",
             "input": {"task_id":"RETURN-HOME-001","frame":"LOCAL_NED","home_position_m":[0.0,0.0],"return_altitude_m":3.0,"horizontal_tolerance_m":0.5,"altitude_tolerance_m":0.5,"timeout_s":30.0}},
            {"step_id": "land", "capability_id": "LAND-001", "post_action": "none",
             "input": {"task_id":"LAND-001","frame":"LOCAL_NED","ground_altitude_tolerance_m":0.5,"horizontal_tolerance_m":1.0,"timeout_s":30.0}},
        ],
    }


async def run(args: argparse.Namespace) -> int:
    out = ensure_dir(Path(args.report_dir) / f"comparative_{stamp()}")
    write_json(out / "source_manifest.json", source_manifest())
    write_json(out / "environment.json", environment_manifest())
    rows: list[dict] = []
    cycle_rows: list[dict] = []
    targets = [[5.0,0.0,-3.0],[0.0,5.0,-3.0],[-5.0,0.0,-3.0],[0.0,-5.0,-3.0]]
    simulator = SimulationSupervisor(PROJECT)

    for i in range(1, args.cycles + 1):
        target = targets[(i - 1) % len(targets)]
        scenario = f"EVAL-COMP-{i:03d}"
        print(f"COMP_CYCLE_START {i}/{args.cycles} target={target}")
        try:
            ready = None
            startup_attempt = 0
            startup_error = None
            for startup_attempt in range(1, args.startup_retries + 1):
                try:
                    simulator.start_new([0.0,0.0,-0.0])
                    ready = await wait_flight_ready(timeout_s=args.readiness_wait_s, stable_s=args.readiness_stable_s)
                    print(f"COMP_SIM_READY cycle={i} startup_attempt={startup_attempt} state={ready}")
                    break
                except Exception as exc:
                    startup_error = exc
                    print(
                        f"COMP_SIM_RETRY cycle={i} startup_attempt={startup_attempt}/{args.startup_retries} "
                        f"error={type(exc).__name__}: {exc}"
                    )
                    try:
                        simulator.stop()
                    except Exception:
                        pass
                    if startup_attempt < args.startup_retries:
                        await asyncio.sleep(args.startup_retry_cooldown_s)
            if ready is None:
                assert startup_error is not None
                raise RuntimeError(
                    f"simulator/flight readiness failed after {args.startup_retries} attempts: "
                    f"{type(startup_error).__name__}: {startup_error}"
                ) from startup_error
            result = await MissionExperimentRunner(PROJECT).run(
                mission_data=mission(target), scenario_id=scenario, adapter=PX4SITLAdapter()
            )
            cycle_rows.append({"cycle":i,"scenario":scenario,"mission_overall":result.overall,"mission_attempt":str(result.attempt_dir),"startup_attempts":startup_attempt,"error":None})
            for step in result.step_results:
                rel = step.get("attempt_relpath")
                if not rel:
                    cycle_rows.append({"cycle":i,"scenario":scenario,"mission_overall":result.overall,"mission_attempt":str(result.attempt_dir),"error":step.get("runner_error")})
                    continue
                from src.runtime_paths import runs_root
                attempt = runs_root(PROJECT) / rel
                row = extract_attempt_row(attempt, step["capability_id"], condition="comparative")
                row.update({"cycle": i, "scenario": scenario, "step_id": step["step_id"]})
                rows.append(row)
                print(f"COMP_STEP cycle={i} capability={row['capability']} module={row['module_status']} verifier={row['verifier_overall']} class={row['classification']}")
        except Exception as exc:
            cycle_rows.append({"cycle":i,"scenario":scenario,"mission_overall":"ERROR","mission_attempt":None,"startup_attempts":locals().get("startup_attempt", 0),"error":f"{type(exc).__name__}: {exc}"})
            print(f"COMP_CYCLE_ERROR cycle={i} {type(exc).__name__}: {exc}")
            if args.stop_on_error:
                traceback.print_exc()
                break
        finally:
            try:
                simulator.stop()
            except Exception:
                pass
            if i < args.cycles and args.restart_cooldown_s > 0:
                print(f"COMP_RESTART_COOLDOWN seconds={args.restart_cooldown_s:g}")
                await asyncio.sleep(args.restart_cooldown_s)

    capabilities = ["TAKEOFF-001","NAV-001","RETURN-HOME-001","LAND-001"]
    summary: list[dict] = []
    for cap in capabilities:
        cap_rows = [r for r in rows if r["capability"] == cap]
        counts = {k: sum(r["classification"] == k for r in cap_rows) for k in ["AGREE_PASS","AGREE_FAIL","SILENT_FAIL","V_ONLY_LENIENT"]}
        summary.append({
            "Capability": cap,
            "N": len(cap_rows),
            "Agree-P": counts["AGREE_PASS"],
            "Agree-F": counts["AGREE_FAIL"],
            "Silent fail.": counts["SILENT_FAIL"],
            "V-only lenient": counts["V_ONLY_LENIENT"],
        })

    serial_rows = []
    for r in rows:
        rr = dict(r)
        rr["checks"] = json.dumps(rr.get("checks"), sort_keys=True)
        rr["input"] = json.dumps(rr.get("input"), sort_keys=True)
        serial_rows.append(rr)
    write_csv(out / "comparative_runs.csv", serial_rows)
    write_csv(out / "comparative_summary.csv", summary)
    write_json(out / "comparative_summary.json", {"cycles_requested":args.cycles,"rows":summary,"cycle_results":cycle_rows})
    print(f"COMPARATIVE_REPORT={out}")
    for row in summary:
        print("COMPARATIVE_SUMMARY " + " ".join(f"{k}={v}" for k,v in row.items()))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Real-run module-vs-verifier comparative evaluation")
    ap.add_argument("--cycles", type=int, default=10)
    ap.add_argument("--report-dir", default=str(PROJECT / "reports" / "evaluation"))
    ap.add_argument("--stop-on-error", action="store_true")
    ap.add_argument("--readiness-wait-s", type=float, default=60.0)
    ap.add_argument("--readiness-stable-s", type=float, default=2.0)
    ap.add_argument("--restart-cooldown-s", type=float, default=3.0)
    ap.add_argument("--startup-retries", type=int, default=3)
    ap.add_argument("--startup-retry-cooldown-s", type=float, default=3.0)
    args = ap.parse_args()
    if args.cycles <= 0:
        raise SystemExit("--cycles must be > 0")
    if args.readiness_wait_s <= 0:
        raise SystemExit("--readiness-wait-s must be > 0")
    if args.readiness_stable_s < 0:
        raise SystemExit("--readiness-stable-s must be >= 0")
    if args.restart_cooldown_s < 0:
        raise SystemExit("--restart-cooldown-s must be >= 0")
    if args.startup_retries <= 0:
        raise SystemExit("--startup-retries must be > 0")
    if args.startup_retry_cooldown_s < 0:
        raise SystemExit("--startup-retry-cooldown-s must be >= 0")
    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())
