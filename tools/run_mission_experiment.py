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

from orchestration.mission_experiment import MissionExperimentRunner
from orchestration.step37a_simulation import SimulationSupervisor
from src.adapters.px4_sitl_adapter import PX4SITLAdapter
from mission_startup_readiness import close_adapter, wait_for_localization_ready


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run MISSION-001 through independently verified child capability "
            "windows and the deterministic mission verifier."
        )
    )
    parser.add_argument("--mission-file", required=True)
    parser.add_argument("--scenario", default="MISSION-V60")
    parser.add_argument("--reset-simulation", action="store_true")
    parser.add_argument("--spawn-north", type=float, default=0.0)
    parser.add_argument("--spawn-east", type=float, default=0.0)
    parser.add_argument("--spawn-altitude", type=float, default=0.0)
    parser.add_argument("--stop-simulation-after", action="store_true")
    return parser.parse_args()


async def _run(args: argparse.Namespace) -> int:
    mission_path = Path(args.mission_file)
    if not mission_path.is_absolute():
        mission_path = PROJECT / mission_path
    mission_data = json.loads(mission_path.read_text(encoding="utf-8"))
    if not isinstance(mission_data, dict):
        raise ValueError("mission file must contain one JSON object")

    simulator = SimulationSupervisor(PROJECT)
    readiness_probe: PX4SITLAdapter | None = None

    try:
        if args.reset_simulation:
            start_ned = [
                float(args.spawn_north),
                float(args.spawn_east),
                -float(args.spawn_altitude),
            ]
            status = simulator.start_new(start_ned)
            print(
                "SIMULATION_CONTAINER_READY "
                f"container={status['container_name']} "
                f"spawn_ned={status['spawn_position_ned_m']}"
            )

            # Container-running is not yet equivalent to PX4 local-position
            # readiness.  Wait read-only for the same localization precondition
            # TAKEOFF-001 requires.  No arm/takeoff action occurs here.
            readiness_probe = PX4SITLAdapter()
            readiness = await wait_for_localization_ready(
                readiness_probe,
                timeout_s=15.0,
                poll_s=0.2,
            )
            await close_adapter(readiness_probe)
            readiness_probe = None

            print(
                "SIMULATION_READY "
                f"container={status['container_name']} "
                f"spawn_ned={status['spawn_position_ned_m']} "
                f"localization_valid={str(readiness['localization_valid']).lower()} "
                f"position_ned={readiness['position_m']}"
            )

        runner = MissionExperimentRunner(PROJECT)
        result = await runner.run(
            mission_data=mission_data,
            scenario_id=args.scenario,
            adapter=PX4SITLAdapter(),
        )
        for step in result.step_results:
            print(
                "MISSION_STEP "
                f"sequence={step['sequence']} "
                f"step={step['step_id']} "
                f"capability={step['capability_id']} "
                f"overall={step['overall']} "
                f"attempt={step.get('attempt_relpath')}"
            )
            if step.get("post_action_error"):
                print(
                    "MISSION_STEP_POST_ACTION_WARNING "
                    f"step={step['step_id']} error={step['post_action_error']}"
                )
            if step.get("runner_error"):
                print(
                    "MISSION_STEP_RUNNER_ERROR "
                    f"step={step['step_id']} error={step['runner_error']}"
                )
        print(
            "MISSION_RESULT "
            f"mission={result.mission_id} "
            f"overall={result.overall} "
            f"attempt={result.attempt_dir}"
        )
        print(json.dumps(result.verification, indent=2, sort_keys=True))
        if result.adapter_cleanup_error:
            print(f"ADAPTER_CLEANUP_WARNING {result.adapter_cleanup_error}")
        return 0 if result.overall == "PASS" else 2
    finally:
        if readiness_probe is not None:
            try:
                await close_adapter(readiness_probe)
            except Exception as exc:
                print(
                    f"MISSION_READINESS_CLEANUP_WARNING "
                    f"{type(exc).__name__}: {exc}",
                    file=sys.stderr,
                )
        if args.stop_simulation_after:
            simulator.stop()


def main() -> int:
    try:
        return asyncio.run(_run(parse_args()))
    except Exception as exc:
        print(f"MISSION_RUN_ERROR {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
