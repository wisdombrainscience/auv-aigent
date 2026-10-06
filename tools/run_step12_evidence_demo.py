import asyncio
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from src.adapters.px4_sitl_adapter import PX4SITLAdapter
from src.evidence.attempt import AttemptEvidence
from src.telemetry.recorder import TelemetryRecorder


async def main() -> None:
    evidence = AttemptEvidence(
        root=PROJECT_ROOT / "runs",
        task_id="NAV-001",
        scenario_id="STEP12-DEMO",
        attempt=1,
    )

    if evidence.path.exists():
        raise RuntimeError(
            f"Attempt already exists: {evidence.path}"
        )

    evidence.create()

    evidence.write_json(
        "input.json",
        {
            "task_id": "NAV-001",
            "scenario_id": "STEP12-DEMO",
            "frame": "LOCAL_NED",
            "target_position_m": [0.0, 0.0, 0.0],
            "position_tolerance_m": 0.5,
            "timeout_s": 30,
        },
    )

    evidence.write_metadata(
        {
            "simulator": "PX4 SITL SIH",
            "adapter": "PX4SITLAdapter",
            "evidence_demo": True,
        }
    )

    evidence.append_event("attempt_created")

    adapter = PX4SITLAdapter()

    evidence.append_event("px4_connect_started")
    await adapter.connect()
    evidence.append_event("px4_connected")

    recorder = TelemetryRecorder(
        output_path=evidence.path / "telemetry.csv",
        sample_provider=adapter.get_telemetry_sample,
        rate_hz=5.0,
    )

    evidence.append_event(
        "telemetry_recording_started"
    )

    samples = await recorder.record_for(3.0)

    evidence.append_event(
        "telemetry_recording_finished",
        {"sample_count": samples},
    )

    state = await adapter.get_state()
    position = await adapter.get_position()

    evidence.write_json(
        "module_output.json",
        {
            "status": "evidence_demo_only",
            "position": {
                "x": position.x,
                "y": position.y,
                "z": position.z,
            },
            "state": {
                "connected": state.connected,
                "armed": state.armed,
                "airborne": state.airborne,
                "localization_valid": state.localization_valid,
            },
        },
    )

    evidence.write_json(
        "agent_job.json",
        {
            "role": None,
            "provider": None,
            "model": None,
            "reason": "STEP12 deterministic evidence demo; no AI job",
        },
    )

    evidence.append_event(
        "attempt_ready_for_finalize"
    )

    final_file = evidence.finalize()

    ok, problems = evidence.verify_integrity()

    print()
    print("STEP 12 EVIDENCE DEMO")
    print("=" * 50)
    print(f"Attempt: {evidence.path}")
    print(f"Samples: {samples}")
    print(f"Finalized: {final_file}")
    print(
        "Integrity:",
        "PASS" if ok else "FAIL",
    )

    if problems:
        print(json.dumps(problems, indent=2))

    if not ok:
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
