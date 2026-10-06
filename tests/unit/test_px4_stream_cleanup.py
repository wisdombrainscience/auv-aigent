import asyncio

from src.adapters.px4_sitl_adapter import PX4SITLAdapter


class ClosingStream:
    def __init__(self):
        self.closed = False
        self.sent = False

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self.sent:
            raise StopAsyncIteration
        self.sent = True
        return 123

    async def aclose(self):
        self.closed = True


def test_first_closes_mavsdk_stream_after_single_value():
    adapter = object.__new__(PX4SITLAdapter)
    adapter.telemetry_timeout_s = 1.0
    stream = ClosingStream()
    value = asyncio.run(adapter._first(stream))
    assert value == 123
    assert stream.closed is True


class ActionError(Exception):
    pass


class _FakeAction:
    def __init__(self):
        self.arm_calls = 0
        self.armed = False
        self.airborne = False
        self.takeoff_altitude = None

    async def set_takeoff_altitude(self, value):
        self.takeoff_altitude = value

    async def arm(self):
        self.arm_calls += 1
        if self.arm_calls == 1:
            raise ActionError("COMMAND_DENIED: 'Command Denied'; origin: arm(); params: ()")
        self.armed = True

    async def takeoff(self):
        self.airborne = True


class _FakeDrone:
    def __init__(self):
        self.action = _FakeAction()


def test_prepare_airborne_retries_transient_arm_command_denied():
    from src.adapters.base_uav_adapter import UAVPosition, UAVState

    adapter = object.__new__(PX4SITLAdapter)
    adapter._connected = True
    adapter._drone = _FakeDrone()

    async def get_state():
        return UAVState(
            connected=True,
            armed=adapter._drone.action.armed,
            airborne=adapter._drone.action.airborne,
            localization_valid=True,
        )

    async def get_position():
        return UAVPosition(0.0, 0.0, -2.0)

    adapter.get_state = get_state
    adapter.get_position = get_position

    asyncio.run(adapter.prepare_airborne(-2.0, timeout_s=3.0))

    assert adapter._drone.action.arm_calls == 2
    assert adapter._drone.action.armed is True
    assert adapter._drone.action.airborne is True


class QueueStream:
    def __init__(self):
        self.queue = asyncio.Queue()
        self.closed = False

    def __aiter__(self):
        return self

    async def __anext__(self):
        return await self.queue.get()

    async def aclose(self):
        self.closed = True


class CountingStreamFactory:
    def __init__(self, stream):
        self.stream = stream
        self.calls = 0

    def __call__(self):
        self.calls += 1
        return self.stream


class PumpDrone:
    def __init__(self):
        from types import SimpleNamespace

        self.connection = QueueStream()
        self.armed_stream = QueueStream()
        self.airborne_stream = QueueStream()
        self.health_stream = QueueStream()
        self.position_stream = QueueStream()

        self.connection_factory = CountingStreamFactory(self.connection)
        self.armed_factory = CountingStreamFactory(self.armed_stream)
        self.airborne_factory = CountingStreamFactory(self.airborne_stream)
        self.health_factory = CountingStreamFactory(self.health_stream)
        self.position_factory = CountingStreamFactory(self.position_stream)

        self.core = SimpleNamespace(connection_state=self.connection_factory)
        self.telemetry = SimpleNamespace(
            armed=self.armed_factory,
            in_air=self.airborne_factory,
            health=self.health_factory,
            position_velocity_ned=self.position_factory,
        )


def test_persistent_telemetry_pumps_reuse_one_subscription_per_topic():
    from types import SimpleNamespace

    async def scenario():
        drone = PumpDrone()
        adapter = object.__new__(PX4SITLAdapter)
        adapter._drone = drone
        adapter._connected = True
        adapter._closed = False
        adapter._offboard_started = False
        adapter._last_state = None
        adapter.telemetry_timeout_s = 1.0
        adapter._telemetry_tasks = {}
        adapter._telemetry_values = {}
        adapter._telemetry_events = {}
        adapter._telemetry_errors = {}

        adapter._start_telemetry_pumps()

        await drone.connection.queue.put(SimpleNamespace(is_connected=True))
        await drone.armed_stream.queue.put(False)
        await drone.airborne_stream.queue.put(False)
        await drone.health_stream.queue.put(SimpleNamespace(is_local_position_ok=True))
        await drone.position_stream.queue.put(
            SimpleNamespace(
                position=SimpleNamespace(north_m=1.0, east_m=2.0, down_m=-3.0),
                velocity=SimpleNamespace(north_m_s=0.1, east_m_s=0.2, down_m_s=0.3),
            )
        )

        # Many consumers read snapshots, but stream factories are invoked once.
        for _ in range(25):
            state = await adapter.get_state()
            position = await adapter.get_position()
            sample = await adapter.get_telemetry_sample()
            assert state.localization_valid is True
            assert position.z == -3.0
            assert sample.z == -3.0

        assert drone.connection_factory.calls == 1
        assert drone.armed_factory.calls == 1
        assert drone.airborne_factory.calls == 1
        assert drone.health_factory.calls == 1
        assert drone.position_factory.calls == 1

        await adapter._stop_telemetry_pumps()
        assert drone.connection.closed is True
        assert drone.armed_stream.closed is True
        assert drone.airborne_stream.closed is True
        assert drone.health_stream.closed is True
        assert drone.position_stream.closed is True

    asyncio.run(scenario())


def test_offboard_entry_seeds_current_position_before_destination(monkeypatch):
    from types import MethodType, SimpleNamespace
    from src.adapters.base_uav_adapter import UAVPosition
    import src.adapters.px4_sitl_adapter as px4_module

    class FakePositionNedYaw:
        def __init__(self, north_m, east_m, down_m, yaw_deg):
            self.north_m = north_m
            self.east_m = east_m
            self.down_m = down_m
            self.yaw_deg = yaw_deg

    monkeypatch.setattr(px4_module, "PositionNedYaw", FakePositionNedYaw)

    class FakeOffboard:
        def __init__(self):
            self.targets = []
            self.starts = 0

        async def set_position_ned(self, target):
            self.targets.append(target)

        async def start(self):
            self.starts += 1

    async def scenario():
        offboard = FakeOffboard()
        adapter = object.__new__(PX4SITLAdapter)
        adapter._connected = True
        adapter._offboard_started = False
        adapter._drone = SimpleNamespace(offboard=offboard)

        async def fake_get_position(self):
            return UAVPosition(1.0, 2.0, -2.0)

        adapter.get_position = MethodType(fake_get_position, adapter)

        await adapter.send_position_target(6.0, 7.0, -3.0, 15.0)

        assert offboard.starts == 1
        assert len(offboard.targets) == 2
        seed, destination = offboard.targets
        assert (seed.north_m, seed.east_m, seed.down_m) == (1.0, 2.0, -2.0)
        assert (destination.north_m, destination.east_m, destination.down_m) == (6.0, 7.0, -3.0)
        assert adapter._offboard_started is True

    asyncio.run(scenario())


def test_land_stabilizes_hold_before_native_land():
    async def scenario():
        adapter = object.__new__(PX4SITLAdapter)
        calls = []

        async def hold_position(**kwargs):
            calls.append(("hold", kwargs))

        async def land_and_wait(timeout_s=30.0):
            calls.append(("land", timeout_s))

        adapter.hold_position = hold_position
        adapter.land_and_wait = land_and_wait

        await adapter.land(timeout_s=10.0)
        assert calls[0][0] == "hold"
        assert calls[0][1]["max_speed_m_s"] == 0.30
        assert calls[1][0] == "land"
        assert 0 < calls[1][1] <= 10.0

    asyncio.run(scenario())
