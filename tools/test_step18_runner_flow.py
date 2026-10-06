from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

PROJECT_ROOT = (
    Path(__file__)
    .resolve()
    .parents[1]
)

sys.path.insert(
    0,
    str(PROJECT_ROOT),
)

from orchestration.runner import (
    ExperimentRunner,
)
from orchestration.runtime_input_bridge import (
    prepare_runtime_attempt,
)
from src.core.base_capability import (
    BaseCapabilityModule,
)
from user_interface.input_schema import (
    UserRuntimeInput,
)


class Step18SmokeCapability(
    BaseCapabilityModule
):
    async def validate_input(
        self,
        input_data,
    ):
        required = {
            "target_position_m",
            "position_tolerance_m",
            "timeout_s",
            "frame",
        }

        missing = (
            required
            - set(input_data)
        )

        if missing:
            raise ValueError(
                f"missing: {missing}"
            )

    async def check_preconditions(
        self,
        input_data,
    ):
        return None

    async def execute(
        self,
        input_data,
    ):
        return {
            "target_received":
                input_data[
                    "target_position_m"
                ],
            "note":
                "STEP18 runner smoke only",
        }

    async def build_result(
        self,
        input_data,
        execution_output,
        elapsed_time_s,
    ):
        return execution_output


async def main() -> None:
    user_input = UserRuntimeInput(
        target_position_m=(
            10.0,
            5.0,
            -3.0,
        ),
        position_tolerance_m=0.5,
        timeout_s=30.0,
        frame="LOCAL_NED",
        scenario_id="STEP18-RUNNER",
    )

    prepared = prepare_runtime_attempt(
        runtime_input=user_input,
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

    runner = ExperimentRunner(
        attempt_dir=(
            prepared.attempt_dir
        ),
        capability=(
            Step18SmokeCapability()
        ),
    )

    summary = await runner.run()

    print(
        json.dumps(
            summary.to_dict(),
            indent=2,
            sort_keys=True,
        )
    )

    print()
    print("STEP18 RUNNER FLOW: PASS")


if __name__ == "__main__":
    asyncio.run(main())
