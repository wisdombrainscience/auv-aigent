from __future__ import annotations

import asyncio
import csv
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Awaitable, Callable


@dataclass(frozen=True)
class TelemetrySample:
    timestamp_s: float

    x: float
    y: float
    z: float

    vx: float
    vy: float
    vz: float

    vehicle_state: str
    localization_valid: bool


class TelemetryRecorder:
    def __init__(
        self,
        output_path: Path,
        sample_provider: Callable[[], Awaitable[TelemetrySample]],
        rate_hz: float = 10.0,
        flush_every_samples: int = 5,
    ) -> None:
        if rate_hz <= 0:
            raise ValueError("rate_hz must be > 0")
        if flush_every_samples <= 0:
            raise ValueError("flush_every_samples must be > 0")

        self.output_path = Path(output_path)
        self.sample_provider = sample_provider
        self.rate_hz = rate_hz
        self.flush_every_samples = int(flush_every_samples)

    async def record_for(self, duration_s: float) -> int:
        if duration_s <= 0:
            raise ValueError("duration_s must be > 0")

        self.output_path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        interval = 1.0 / self.rate_hz
        deadline = time.monotonic() + duration_s
        count = 0

        with self.output_path.open(
            "w",
            newline="",
            encoding="utf-8",
        ) as f:
            writer = csv.DictWriter(
                f,
                fieldnames=[
                    "timestamp",
                    "x",
                    "y",
                    "z",
                    "vx",
                    "vy",
                    "vz",
                    "vehicle_state",
                    "localization_valid",
                ],
            )

            writer.writeheader()

            while time.monotonic() < deadline:
                sample = await self.sample_provider()

                writer.writerow(
                    {
                        "timestamp": sample.timestamp_s,
                        "x": sample.x,
                        "y": sample.y,
                        "z": sample.z,
                        "vx": sample.vx,
                        "vy": sample.vy,
                        "vz": sample.vz,
                        "vehicle_state": sample.vehicle_state,
                        "localization_valid": sample.localization_valid,
                    }
                )

                count += 1
                if count % self.flush_every_samples == 0:
                    f.flush()

                await asyncio.sleep(interval)

            f.flush()

        return count
