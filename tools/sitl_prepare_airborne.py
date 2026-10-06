#!/usr/bin/env python3
"""
PX4 SITL-only experiment fixture for NAV-001.

This script intentionally refuses non-local endpoints. It prepares only a
simulated PX4 vehicle so GoToTargetModule starts with its frozen preconditions:
connected=True, armed=True, airborne=True, localization_valid=True.

It is NOT for real UAV hardware.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
from datetime import datetime, timezone
from pathlib import Path

from mavsdk import System


ALLOWED_ENDPOINTS = {
    "udpin://0.0.0.0:14540",
    "udp://127.0.0.1:14540",
    "udp://localhost:14540",
}


async def wait_match(agen, predicate, timeout_s: float, label: str):
    async def _inner():
        async for value in agen:
            if predicate(value):
                return value
    try:
        return await asyncio.wait_for(_inner(), timeout=timeout_s)
    except asyncio.TimeoutError as exc:
        raise RuntimeError(f"timeout waiting for {label}") from exc


async def latest_position(drone: System, timeout_s: float = 5.0):
    pv = await wait_match(
        drone.telemetry.position_velocity_ned(),
        lambda _: True,
        timeout_s,
        "local NED position",
    )
    p = pv.position
    return {
        "x": float(p.north_m),
        "y": float(p.east_m),
        "z": float(p.down_m),
    }


async def main_async(args) -> int:
    if not args.simulation_only:
        raise SystemExit(
            "REFUSED: pass --simulation-only. This fixture is for PX4 SITL only."
        )
    if args.system_address not in ALLOWED_ENDPOINTS:
        raise SystemExit(
            "REFUSED: non-local endpoint. Allowed SITL endpoints: "
            + ", ".join(sorted(ALLOWED_ENDPOINTS))
        )

    report = {
        "mode": "SIMULATION_ONLY",
        "system_address": args.system_address,
        "requested_takeoff_altitude_m": args.takeoff_altitude,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "status": "FAIL",
    }

    drone = System()
    print("SITL FIXTURE: connecting...")
    await drone.connect(system_address=args.system_address)

    await wait_match(
        drone.core.connection_state(),
        lambda s: bool(s.is_connected),
        args.connect_timeout,
        "SITL connection",
    )
    print("SITL FIXTURE: connected")

    # Wait for local position/localization to become available.
    await wait_match(
        drone.telemetry.health(),
        lambda h: bool(h.is_local_position_ok),
        args.health_timeout,
        "local position health",
    )
    print("SITL FIXTURE: local position valid")

    before = await latest_position(drone)
    report["position_before"] = before

    armed_before = await wait_match(
        drone.telemetry.armed(),
        lambda _: True,
        5.0,
        "armed telemetry",
    )
    in_air_before = await wait_match(
        drone.telemetry.in_air(),
        lambda _: True,
        5.0,
        "in_air telemetry",
    )
    report["armed_before"] = bool(armed_before)
    report["airborne_before"] = bool(in_air_before)

    if not armed_before:
        print("SITL FIXTURE: arming simulated vehicle")
        await drone.action.arm()

    await drone.action.set_takeoff_altitude(args.takeoff_altitude)

    if not in_air_before:
        print("SITL FIXTURE: takeoff in simulation")
        await drone.action.takeoff()

    await wait_match(
        drone.telemetry.armed(),
        lambda value: bool(value),
        args.takeoff_timeout,
        "armed=True",
    )
    await wait_match(
        drone.telemetry.in_air(),
        lambda value: bool(value),
        args.takeoff_timeout,
        "airborne=True",
    )

    async def altitude_ready():
        async for pv in drone.telemetry.position_velocity_ned():
            z = float(pv.position.down_m)
            if z <= -args.min_airborne_height:
                return {
                    "x": float(pv.position.north_m),
                    "y": float(pv.position.east_m),
                    "z": z,
                }

    try:
        ready_pos = await asyncio.wait_for(
            altitude_ready(),
            timeout=args.takeoff_timeout,
        )
    except asyncio.TimeoutError as exc:
        raise RuntimeError(
            "simulated vehicle became airborne but did not reach "
            "the minimum local NED height"
        ) from exc

    # Give the simulated vehicle a short time to settle before the experiment.
    await asyncio.sleep(args.settle_seconds)
    after = await latest_position(drone)
    report["position_ready"] = after

    horizontal_from_origin = math.hypot(after["x"], after["y"])
    report["horizontal_distance_from_origin_m"] = horizontal_from_origin

    if horizontal_from_origin > args.max_origin_xy:
        raise RuntimeError(
            "fixture start is not reproducible: horizontal position is "
            f"{horizontal_from_origin:.3f} m from local origin, "
            f"allowed <= {args.max_origin_xy:.3f} m"
        )

    if after["z"] > -args.min_airborne_height:
        raise RuntimeError(
            f"fixture altitude invalid: z={after['z']:.3f}, "
            f"expected <= {-args.min_airborne_height:.3f}"
        )

    report.update(
        {
            "connected": True,
            "armed": True,
            "airborne": True,
            "localization_valid": True,
            "status": "PASS",
        }
    )

    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    print("SITL FIXTURE: PASS")
    print(
        "READY POSITION: "
        f"x={after['x']:.3f} y={after['y']:.3f} z={after['z']:.3f}"
    )
    print(f"REPORT={args.report}")
    return 0


def parse_args():
    ap = argparse.ArgumentParser()
    ap.add_argument("--simulation-only", action="store_true")
    ap.add_argument(
        "--system-address",
        default="udpin://0.0.0.0:14540",
    )
    ap.add_argument("--takeoff-altitude", type=float, default=2.0)
    ap.add_argument("--min-airborne-height", type=float, default=1.5)
    ap.add_argument("--max-origin-xy", type=float, default=1.5)
    ap.add_argument("--settle-seconds", type=float, default=2.0)
    ap.add_argument("--connect-timeout", type=float, default=15.0)
    ap.add_argument("--health-timeout", type=float, default=20.0)
    ap.add_argument("--takeoff-timeout", type=float, default=20.0)
    ap.add_argument(
        "--report",
        type=Path,
        default=Path("reports/pipeline_v2/sitl_fixture.json"),
    )
    return ap.parse_args()


def main() -> int:
    args = parse_args()
    try:
        return asyncio.run(main_async(args))
    except KeyboardInterrupt:
        print("SITL FIXTURE: interrupted")
        return 130
    except Exception as exc:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(
            json.dumps(
                {
                    "mode": "SIMULATION_ONLY",
                    "status": "FAIL",
                    "reason": str(exc),
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        print(f"SITL FIXTURE: FAIL: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
