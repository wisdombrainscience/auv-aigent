import asyncio
import csv
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from src.adapters.px4_sitl_adapter import PX4SITLAdapter
from src.telemetry.recorder import TelemetryRecorder


async def main() -> None:
    adapter = PX4SITLAdapter()

    print("Connecting...")
    await adapter.connect()
    print("CONNECT: PASS")

    output = PROJECT_ROOT / "runs" / "STEP12" / "telemetry.csv"

    recorder = TelemetryRecorder(
        output_path=output,
        sample_provider=adapter.get_telemetry_sample,
        rate_hz=5.0,
    )

    print("Recording telemetry for 3 seconds...")
    count = await recorder.record_for(3.0)

    print(f"SAMPLES: {count}")
    print(f"FILE: {output}")

    with output.open(
        newline="",
        encoding="utf-8",
    ) as f:
        rows = list(csv.DictReader(f))

    assert rows, "telemetry.csv contains no samples"

    required = {
        "timestamp",
        "x",
        "y",
        "z",
        "vx",
        "vy",
        "vz",
        "vehicle_state",
        "localization_valid",
    }

    assert required.issubset(rows[0].keys())

    assert all(
        row["localization_valid"] == "True"
        for row in rows
    )

    print("TELEMETRY CSV: PASS")
    print("FIELDS: PASS")
    print("LOCALIZATION: PASS")


if __name__ == "__main__":
    asyncio.run(main())
