from pathlib import Path

import pytest

from orchestration.runtime_input_bridge import (
    next_attempt_number,
    prepare_runtime_attempt,
)
from user_interface.input_builder import (
    InputBuildError,
)
from user_interface.input_schema import (
    UserRuntimeInput,
)
from user_interface.policy import (
    load_input_policy,
)


POLICY = """
version: 1
status: provisional_simulation_only

supported_frames:
  - LOCAL_NED

test_area_m:
  x_min: -20.0
  x_max: 20.0
  y_min: -20.0
  y_max: 20.0
  z_min: -10.0
  z_max: 0.0
"""


def write_policy(
    tmp_path: Path,
) -> Path:
    path = (
        tmp_path
        / "input_policy.yaml"
    )

    path.write_text(
        POLICY,
        encoding="utf-8",
    )

    return path


def valid_input():
    return UserRuntimeInput(
        target_position_m=(
            10.0,
            5.0,
            -3.0,
        ),
        position_tolerance_m=0.5,
        timeout_s=30.0,
        frame="LOCAL_NED",
        scenario_id="T01",
    )


def test_policy_loads(
    tmp_path: Path,
):
    policy = load_input_policy(
        write_policy(tmp_path)
    )

    assert policy.version == 1

    assert (
        "LOCAL_NED"
        in policy.supported_frames
    )

    assert policy.test_area.contains(
        10.0,
        5.0,
        -3.0,
    )


def test_valid_attempt_is_prepared(
    tmp_path: Path,
):
    policy = write_policy(
        tmp_path
    )

    runs = tmp_path / "runs"

    prepared = prepare_runtime_attempt(
        runtime_input=valid_input(),
        policy_path=policy,
        runs_root=runs,
    )

    assert (
        prepared.attempt_number
        == 1
    )

    assert (
        prepared.attempt_dir
        / "input.json"
    ).exists()

    assert (
        prepared.attempt_dir
        / "raw_user_input.json"
    ).exists()

    assert (
        prepared.attempt_dir
        / "input_validation.json"
    ).exists()

    assert (
        prepared.attempt_dir
        / "events.jsonl"
    ).exists()

    assert (
        prepared.attempt_dir
        / "metadata.json"
    ).exists()

    # Valid preparation is NOT finalized.
    # Simulator/module execution still follows.
    assert not (
        prepared.attempt_dir
        / "finalized.json"
    ).exists()


def test_attempt_number_increments(
    tmp_path: Path,
):
    policy = write_policy(
        tmp_path
    )

    runs = tmp_path / "runs"

    first = prepare_runtime_attempt(
        runtime_input=valid_input(),
        policy_path=policy,
        runs_root=runs,
    )

    second = prepare_runtime_attempt(
        runtime_input=valid_input(),
        policy_path=policy,
        runs_root=runs,
    )

    assert (
        first.attempt_number
        == 1
    )

    assert (
        second.attempt_number
        == 2
    )


def test_invalid_attempt_is_finalized(
    tmp_path: Path,
):
    policy = write_policy(
        tmp_path
    )

    runs = tmp_path / "runs"

    invalid = UserRuntimeInput(
        target_position_m=(
            100.0,
            0.0,
            -3.0,
        ),
        position_tolerance_m=0.5,
        timeout_s=30.0,
        frame="LOCAL_NED",
        scenario_id="BAD01",
    )

    with pytest.raises(
        InputBuildError
    ):
        prepare_runtime_attempt(
            runtime_input=invalid,
            policy_path=policy,
            runs_root=runs,
        )

    attempt = (
        runs
        / "NAV-001"
        / "BAD01"
        / "attempt_001"
    )

    assert (
        attempt
        / "raw_user_input.json"
    ).exists()

    assert (
        attempt
        / "input_validation.json"
    ).exists()

    assert not (
        attempt
        / "input.json"
    ).exists()

    assert (
        attempt
        / "finalized.json"
    ).exists()


def test_next_attempt_without_runs(
    tmp_path: Path,
):
    result = next_attempt_number(
        runs_root=tmp_path,
        task_id="NAV-001",
        scenario_id="T01",
    )

    assert result == 1


def test_input_policy_can_resolve_canonical_experiment_region(tmp_path: Path):
    experiment = tmp_path / "experiment.yaml"
    experiment.write_text(
        """permitted_test_region:\n  x_m: {min: -2, max: 2}\n  y_m: {min: -3, max: 3}\n  z_m: {min: -4, max: 0}\n""",
        encoding="utf-8",
    )
    policy_path = tmp_path / "input_policy.yaml"
    policy_path.write_text(
        """version: 1\nstatus: test\nsupported_frames: [LOCAL_NED]\ntest_area_source: experiment.yaml\n""",
        encoding="utf-8",
    )
    policy = load_input_policy(policy_path)
    assert policy.test_area.contains(2, 3, 0)
    assert not policy.test_area.contains(2.1, 0, -1)
