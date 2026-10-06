import asyncio
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from src.adapters.px4_sitl_adapter import PX4SITLAdapter
from src.evidence.attempt import AttemptEvidence
from src.telemetry.recorder import TelemetryRecorder
from src.verification.goto_target_verifier import GoToTargetVerifier


async def main() -> None:
    evidence = AttemptEvidence(
        root=PROJECT_ROOT / "runs",
        task_id="NAV-001",
        scenario_id="STEP13-DEMO",
        attempt=2,
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
            "scenario_id": "STEP13-DEMO",
            "frame": "LOCAL_NED",
            "target_position_m": [0.0, 0.0, 0.0],
            "position_tolerance_m": 0.5,
            "timeout_s": 30.0,
        },
    )

    evidence.write_metadata(
        {
            "simulator": "PX4 SITL SIH",
            "adapter": "PX4SITLAdapter",
            "workflow": "record -> verify -> finalize",
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

    evidence.append_event("telemetry_started")

    samples = await recorder.record_for(3.0)

    evidence.append_event(
        "telemetry_finished",
        {"sample_count": samples},
    )

    state = await adapter.get_state()
    position = await adapter.get_position()

    # Intentionally do not trust this for official verdict.
    evidence.write_json(
        "module_output.json",
        {
            "status": "completed",
            "final_position": [
                position.x,
                position.y,
                position.z,
            ],
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
            "reason": "deterministic STEP13 verifier demo",
        },
    )

    # Independent verification happens BEFORE finalization.
    verifier = GoToTargetVerifier(
        PROJECT_ROOT / "specs" / "NAV-001.yaml"
    )

    result = verifier.verify_attempt(
        evidence.path,
        write_result=True,
    )

    evidence.append_event(
        "verification_completed",
        {
            "overall": result.overall,
            "position_error_m": result.position_error_m,
            "elapsed_time_s": result.elapsed_time_s,
        },
    )

    # Now verification.json becomes part of immutable evidence.
    final_file = evidence.finalize()

    integrity_ok, problems = evidence.verify_integrity()

    print()
    print("STEP 13 VERIFIED ATTEMPT")
    print("=" * 60)
    print(f"Attempt       : {evidence.path}")
    print(f"Samples       : {samples}")
    print(f"Verifier      : {result.overall}")
    print(f"Position error: {result.position_error_m}")
    print(f"Elapsed       : {result.elapsed_time_s}")
    print(f"Finalized     : {final_file}")
    print(
        "Integrity     :",
        "PASS" if integrity_ok else "FAIL",
    )

    if problems:
        print(json.dumps(problems, indent=2))

    if not integrity_ok:
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
