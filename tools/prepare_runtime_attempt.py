from __future__ import annotations

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

from orchestration.runtime_input_bridge import (
    prepare_runtime_attempt,
)
from user_interface.cli import (
    build_parser,
)
from user_interface.input_builder import (
    InputBuildError,
)
from user_interface.input_schema import (
    UserRuntimeInput,
)


def main() -> int:
    parser = build_parser()

    parser.add_argument(
        "--policy",
        default=str(
            PROJECT_ROOT
            / "config"
            / "input_policy.yaml"
        ),
    )

    parser.add_argument(
        "--runs-root",
        default=str(
            PROJECT_ROOT / "runs"
        ),
    )

    args = parser.parse_args()

    runtime_input = UserRuntimeInput(
        target_position_m=(
            args.target_x,
            args.target_y,
            args.target_z,
        ),
        position_tolerance_m=args.tolerance,
        timeout_s=args.timeout,
        frame=args.frame,
        scenario_id=args.scenario_id,
    )

    try:
        prepared = prepare_runtime_attempt(
            runtime_input=runtime_input,
            policy_path=Path(
                args.policy
            ),
            runs_root=Path(
                args.runs_root
            ),
            task_id="NAV-001",
        )

    except InputBuildError as exc:
        print(
            json.dumps(
                {
                    "status": "INVALID_INPUT",
                    "failure_code": "INVALID_INPUT",
                    "errors": list(
                        exc.result.errors
                    ),
                },
                indent=2,
                sort_keys=True,
            ),
            file=sys.stderr,
        )

        return 2

    print(
        json.dumps(
            {
                "status": "INPUT_READY",
                "attempt_number":
                    prepared.attempt_number,
                "attempt_dir":
                    str(
                        prepared.attempt_dir
                    ),
                "input":
                    prepared
                    .canonical_input
                    .to_dict(),
            },
            indent=2,
            sort_keys=True,
        )
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
