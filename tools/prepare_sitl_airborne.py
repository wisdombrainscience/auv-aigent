from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

PROJECT_ROOT = (
    Path(__file__).resolve().parents[1]
)
sys.path.insert(0, str(PROJECT_ROOT))

from mavsdk import System


SYSTEM_ADDRESS = (
    "udpin://0.0.0.0:14540"
)


async def first(stream):
    async for value in stream:
        return value

    raise RuntimeError(
        "Telemetry stream ended"
    )


async def main() -> None:
    print(
        "SIMULATION ONLY: connecting "
        "to PX4 SITL..."
    )

    drone = System()

    await drone.connect(
        system_address=SYSTEM_ADDRESS
    )

    deadline = time.monotonic() + 15.0

    while True:
        state = await first(
            drone.core.connection_state()
        )

        if state.is_connected:
            break

        if time.monotonic() >= deadline:
            raise TimeoutError(
                "PX4 SITL connection timeout"
            )

        await asyncio.sleep(0.2)

    print("CONNECT: PASS")

    health = await first(
        drone.telemetry.health()
    )

    print(
        "LOCAL POSITION:",
        health.is_local_position_ok,
    )

    if not health.is_local_position_ok:
        raise RuntimeError(
            "PX4 SITL local position "
            "is not valid"
        )

    armed = await first(
        drone.telemetry.armed()
    )

    in_air = await first(
        drone.telemetry.in_air()
    )

    if in_air:
        print(
            "AIRBORNE: already true"
        )
        return

    if not armed:
        print(
            "Arming simulated vehicle..."
        )
        await drone.action.arm()

    # Approximately 2 m initial altitude.
    await drone.action.set_takeoff_altitude(
        2.0
    )

    print(
        "Taking off in simulation..."
    )
    await drone.action.takeoff()

    deadline = time.monotonic() + 15.0

    while True:
        in_air = await first(
            drone.telemetry.in_air()
        )

        position = await first(
            drone.telemetry
            .position_velocity_ned()
        )

        z = position.position.down_m

        print(
            f"in_air={in_air}, "
            f"z/down={z:.2f} m"
        )

        if (
            in_air
            and z <= -1.5
        ):
            print("AIRBORNE SETUP: PASS")
            break

        if time.monotonic() >= deadline:
            raise TimeoutError(
                "Simulation did not reach "
                "airborne state"
            )

        await asyncio.sleep(0.5)


if __name__ == "__main__":
    asyncio.run(main())
