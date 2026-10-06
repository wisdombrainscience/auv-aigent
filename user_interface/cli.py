from __future__ import annotations

import argparse
import json
from collections.abc import Sequence

from user_interface.input_schema import (
    UserRuntimeInput,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="uav-input",
        description=(
            "Build runtime input for the "
            "NAV-001 GoToTarget experiment."
        ),
    )

    parser.add_argument(
        "--target-x",
        type=float,
        required=True,
        help="Target north/x position in meters.",
    )

    parser.add_argument(
        "--target-y",
        type=float,
        required=True,
        help="Target east/y position in meters.",
    )

    parser.add_argument(
        "--target-z",
        type=float,
        required=True,
        help=(
            "Target down/z position in meters "
            "for LOCAL_NED."
        ),
    )

    parser.add_argument(
        "--tolerance",
        type=float,
        required=True,
        help="Position tolerance in meters.",
    )

    parser.add_argument(
        "--timeout",
        type=float,
        required=True,
        help="Execution timeout in seconds.",
    )

    parser.add_argument(
        "--frame",
        default="LOCAL_NED",
        help="Coordinate frame.",
    )

    parser.add_argument(
        "--scenario-id",
        default="T01",
        help="Experiment scenario identifier.",
    )

    return parser


def parse_runtime_input(
    argv: Sequence[str] | None = None,
) -> UserRuntimeInput:
    args = build_parser().parse_args(argv)

    return UserRuntimeInput(
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


def main(
    argv: Sequence[str] | None = None,
) -> int:
    runtime_input = parse_runtime_input(argv)

    print(
        json.dumps(
            runtime_input.to_dict(),
            indent=2,
            sort_keys=True,
        )
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
