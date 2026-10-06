import asyncio

from src.adapters.px4_sitl_adapter import PX4SITLAdapter


async def main() -> None:
    adapter = PX4SITLAdapter()

    print("Connecting...")
    await adapter.connect()
    print("CONNECT: PASS")

    state = await adapter.get_state()

    print("STATE:")
    print(f"  connected          = {state.connected}")
    print(f"  armed              = {state.armed}")
    print(f"  airborne           = {state.airborne}")
    print(f"  localization_valid = {state.localization_valid}")

    position = await adapter.get_position()

    print("POSITION LOCAL_NED:")
    print(f"  x/north = {position.x:.3f} m")
    print(f"  y/east  = {position.y:.3f} m")
    print(f"  z/down  = {position.z:.3f} m")


if __name__ == "__main__":
    asyncio.run(main())
