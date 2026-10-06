#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))

from orchestration.capability_experiment import CapabilityExperimentRunner
from orchestration.step37a_simulation import SimulationSupervisor
from src.adapters.px4_sitl_adapter import PX4SITLAdapter


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run one registered UAV capability through the generic executor, "
            "telemetry evidence window and deterministic verifier."
        )
    )
    parser.add_argument("capability_id")
    parser.add_argument("--input-file", required=True)
    parser.add_argument("--scenario", default="MANUAL-CAPABILITY")
    parser.add_argument("--reset-simulation", action="store_true")
    parser.add_argument("--spawn-north", type=float, default=0.0)
    parser.add_argument("--spawn-east", type=float, default=0.0)
    parser.add_argument("--spawn-altitude", type=float, default=0.0)
    parser.add_argument(
        "--post-action",
        choices=("none", "hold", "land"),
        default="none",
    )
    parser.add_argument("--stop-simulation-after", action="store_true")
    return parser.parse_args()


async def _run(args: argparse.Namespace) -> int:
    input_path = Path(args.input_file)
    if not input_path.is_absolute():
        input_path = PROJECT / input_path
    raw = json.loads(input_path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("input file must contain one JSON object")

    simulator = SimulationSupervisor(PROJECT)
    if args.reset_simulation:
        start_ned = [
            float(args.spawn_north),
            float(args.spawn_east),
            -float(args.spawn_altitude),
        ]
        status = simulator.start_new(start_ned)
        print(
            "SIMULATION_READY "
            f"container={status['container_name']} "
            f"spawn_ned={status['spawn_position_ned_m']}"
        )

    try:
        runner = CapabilityExperimentRunner(PROJECT)
        result = await runner.run(
            args.capability_id,
            input_data=raw,
            scenario_id=args.scenario,
            adapter=PX4SITLAdapter(),
            post_action=args.post_action,
        )
        print(
            "CAPABILITY_RESULT "
            f"capability={result.capability_id} "
            f"overall={result.overall} "
            f"attempt={result.attempt_dir}"
        )
        print(json.dumps(result.verification, indent=2, sort_keys=True))
        if result.post_action_error:
            print(f"POST_ACTION_WARNING {result.post_action_error}")
        if result.adapter_cleanup_error:
            print(f"ADAPTER_CLEANUP_WARNING {result.adapter_cleanup_error}")
        return 0 if result.overall == "PASS" else 2
    finally:
        if args.stop_simulation_after:
            simulator.stop()


def main() -> int:
    try:
        return asyncio.run(_run(parse_args()))
    except Exception as exc:
        print(f"CAPABILITY_RUN_ERROR {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
