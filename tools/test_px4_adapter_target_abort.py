import asyncio
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from src.adapters.px4_sitl_adapter import PX4SITLAdapter


async def main() -> None:
    adapter = PX4SITLAdapter()

    print("Connecting...")
    await adapter.connect()
    print("CONNECT: PASS")

    state_before = await adapter.get_state()
    print("STATE BEFORE:")
    print(f"  armed              = {state_before.armed}")
    print(f"  airborne           = {state_before.airborne}")
    print(f"  localization_valid = {state_before.localization_valid}")

    p0 = await adapter.get_position()

    print("POSITION BEFORE:")
    print(f"  x = {p0.x:.3f}")
    print(f"  y = {p0.y:.3f}")
    print(f"  z = {p0.z:.3f}")

    try:
        print("Sending current-position target...")

        await adapter.send_position_target(
            x=p0.x,
            y=p0.y,
            z=p0.z,
            yaw_deg=0.0,
        )

        print("SEND_POSITION_TARGET: PASS")

        await asyncio.sleep(2.0)

        p1 = await adapter.get_position()

        print("POSITION AFTER:")
        print(f"  x = {p1.x:.3f}")
        print(f"  y = {p1.y:.3f}")
        print(f"  z = {p1.z:.3f}")

        await adapter.abort()
        print("ABORT: PASS")

    except Exception:
        try:
            await adapter.abort()
        except Exception:
            pass
        raise


if __name__ == "__main__":
    asyncio.run(main())
