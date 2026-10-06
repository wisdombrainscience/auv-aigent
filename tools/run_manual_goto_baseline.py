from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

PROJECT_ROOT = (
    Path(__file__).resolve().parents[1]
)
sys.path.insert(0, str(PROJECT_ROOT))

from orchestration.runner import ExperimentRunner
from orchestration.runtime_input_bridge import (
    prepare_runtime_attempt,
)
from src.adapters.px4_sitl_adapter import (
    PX4SITLAdapter,
)
from src.capabilities.navigation.go_to_target import (
    GoToTargetModule,
)
from src.evidence.attempt import AttemptEvidence
from src.telemetry.recorder import (
    TelemetryRecorder,
)
from src.verification.goto_target_verifier import (
    GoToTargetVerifier,
)
from user_interface.input_schema import (
    UserRuntimeInput,
)


def evidence_from_attempt(
    attempt_dir: Path,
) -> AttemptEvidence:
    attempt_dir = attempt_dir.resolve()

    attempt_number = int(
        attempt_dir.name.removeprefix(
            "attempt_"
        )
    )

    return AttemptEvidence(
        root=(
            attempt_dir
            .parent
            .parent
            .parent
        ),
        task_id=(
            attempt_dir
            .parent
            .parent
            .name
        ),
        scenario_id=(
            attempt_dir.parent.name
        ),
        attempt=attempt_number,
    )


async def main() -> None:
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--target-x",
        type=float,
        default=10.0,
    )
    parser.add_argument(
        "--target-y",
        type=float,
        default=5.0,
    )
    parser.add_argument(
        "--target-z",
        type=float,
        default=-3.0,
    )
    parser.add_argument(
        "--tolerance",
        type=float,
        default=0.5,
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=30.0,
    )
    parser.add_argument(
        "--scenario-id",
        default="MANUAL-BASELINE",
    )

    args = parser.parse_args()

    runtime_input = UserRuntimeInput(
        target_position_m=(
            args.target_x,
            args.target_y,
            args.target_z,
        ),
        position_tolerance_m=(
            args.tolerance
        ),
        timeout_s=args.timeout,
        frame="LOCAL_NED",
        scenario_id=args.scenario_id,
    )

    prepared = prepare_runtime_attempt(
        runtime_input=runtime_input,
        policy_path=(
            PROJECT_ROOT
            / "config"
            / "input_policy.yaml"
        ),
        runs_root=(
            PROJECT_ROOT / "runs"
        ),
        task_id="NAV-001",
    )

    evidence = evidence_from_attempt(
        prepared.attempt_dir
    )

    evidence.write_json(
        "agent_job.json",
        {
            "role": None,
            "provider": None,
            "model": None,
            "task": "NAV-001",
            "attempt":
                prepared.attempt_number,
            "summary":
                "Human-written manual "
                "known-good control baseline",
        },
    )

    adapter = PX4SITLAdapter()

    print("Connecting adapter...")
    await adapter.connect()

    state = await adapter.get_state()

    print(
        "PRECONDITION STATE:",
        {
            "connected":
                state.connected,
            "armed":
                state.armed,
            "airborne":
                state.airborne,
            "localization_valid":
                state.localization_valid,
        },
    )

    module = GoToTargetModule(
        adapter,
        poll_interval_s=0.1,
    )

    recorder = TelemetryRecorder(
        output_path=(
            prepared.attempt_dir
            / "telemetry.csv"
        ),
        sample_provider=(
            adapter.get_telemetry_sample
        ),
        rate_hz=10.0,
    )

    runner = ExperimentRunner(
        attempt_dir=(
            prepared.attempt_dir
        ),
        capability=module,
    )

    evidence.append_event(
        "telemetry_started"
    )

    telemetry_task = (
        asyncio.create_task(
            recorder.record_for(
                args.timeout + 5.0
            )
        )
    )

    try:
        summary = await runner.run()

        # Keep recording briefly so the final
        # measured state is present in CSV.
        await asyncio.sleep(0.4)

    finally:
        telemetry_task.cancel()

        try:
            await telemetry_task
        except asyncio.CancelledError:
            pass

    evidence.append_event(
        "telemetry_stopped"
    )

    # Important ordering:
    #
    # 1. capability reaches the target
    # 2. terminal telemetry is captured
    # 3. telemetry recording is stopped
    # 4. only then stop PX4 Offboard control
    #
    # This prevents the independent verifier from
    # evaluating vehicle motion that happens after
    # the capability has already completed.
    evidence.append_event(
        "control_cleanup_started"
    )

    await adapter.abort()

    evidence.append_event(
        "control_cleanup_finished"
    )

    verifier = GoToTargetVerifier(
        PROJECT_ROOT
        / "specs"
        / "NAV-001.yaml"
    )

    verification = (
        verifier.verify_attempt(
            prepared.attempt_dir,
            write_result=True,
        )
    )

    evidence.append_event(
        "verification_completed",
        {
            "overall":
                verification.overall,
            "position_error_m":
                verification
                .position_error_m,
            "elapsed_time_s":
                verification
                .elapsed_time_s,
        },
    )

    evidence.finalize()

    integrity_ok, problems = (
        evidence.verify_integrity()
    )

    print()
    print("=" * 60)
    print(
        "MANUAL KNOWN-GOOD "
        "GOTO TARGET BASELINE"
    )
    print("=" * 60)

    print(
        "Attempt:",
        prepared.attempt_dir,
    )

    print(
        "Module status:",
        summary.module_result
        .status_value,
    )

    print(
        "Verifier:",
        verification.overall,
    )

    print(
        "Position error:",
        verification.position_error_m,
    )

    print(
        "Elapsed:",
        verification.elapsed_time_s,
    )

    print(
        "Evidence integrity:",
        (
            "PASS"
            if integrity_ok
            else "FAIL"
        ),
    )

    if problems:
        print(
            json.dumps(
                problems,
                indent=2,
            )
        )

    if (
        verification.overall != "PASS"
        or not integrity_ok
    ):
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
