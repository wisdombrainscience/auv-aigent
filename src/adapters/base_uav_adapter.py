from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(frozen=True)
class UAVPosition:
    x: float
    y: float
    z: float


@dataclass(frozen=True)
class UAVState:
    connected: bool
    armed: bool
    airborne: bool
    localization_valid: bool


class UAVAdapter(ABC):

    @abstractmethod
    async def connect(self) -> None:
        """Connect to the UAV backend."""
        raise NotImplementedError

    @abstractmethod
    async def get_state(self) -> UAVState:
        """Return current vehicle state."""
        raise NotImplementedError

    @abstractmethod
    async def get_position(self) -> UAVPosition:
        """Return current LOCAL_NED position."""
        raise NotImplementedError

    @abstractmethod
    async def send_position_target(
        self,
        x: float,
        y: float,
        z: float,
        yaw_deg: float = 0.0,
    ) -> None:
        """Send a LOCAL_NED position target."""
        raise NotImplementedError

    async def takeoff_to_altitude(
        self,
        target_altitude_m: float,
        timeout_s: float = 30.0,
    ) -> None:
        """Take off to a positive-up local altitude.

        Adapters that support TAKEOFF-001 should override this method.  It is
        intentionally non-abstract so existing test/dummy adapters used by
        unrelated capabilities remain source-compatible.
        """
        raise NotImplementedError("takeoff_to_altitude is not supported")

    async def land(
        self,
        timeout_s: float = 30.0,
    ) -> None:
        """Land and wait for a measured non-airborne state.

        Adapters that support LAND-001 should override this method.  It remains
        non-abstract so existing adapters used by unrelated capabilities stay
        source-compatible.
        """
        raise NotImplementedError("land is not supported")

    @abstractmethod
    async def abort(self) -> None:
        """Abort the current navigation action safely."""
        raise NotImplementedError
