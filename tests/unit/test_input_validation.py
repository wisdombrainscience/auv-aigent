import json
import math
from pathlib import Path

import pytest

from user_interface.input_builder import (
    InputBuildError,
    build_input_artifacts,
)
from user_interface.input_schema import (
    UserRuntimeInput,
)
from user_interface.input_validation import (
    RuntimeInputValidator,
    TestAreaBounds,
)


def make_bounds():
    # Bounds này CHỈ dùng cho unit test.
    # Không phải production policy.
    return TestAreaBounds(
        x_min=-20.0,
        x_max=20.0,
        y_min=-20.0,
        y_max=20.0,
        z_min=-10.0,
        z_max=0.0,
    )


def make_validator():
    return RuntimeInputValidator(
        supported_frames={"LOCAL_NED"},
        test_area=make_bounds(),
    )


def make_valid_input():
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


def test_valid_input_passes():
    result = make_validator().validate(
        make_valid_input()
    )

    assert result.valid is True
    assert result.errors == ()


def test_nan_rejected():
    value = UserRuntimeInput(
        target_position_m=(
            math.nan,
            5.0,
            -3.0,
        ),
        position_tolerance_m=0.5,
        timeout_s=30.0,
    )

    result = make_validator().validate(
        value
    )

    assert result.valid is False
    assert (
        "non_finite_numeric_value"
        in result.errors
    )


def test_infinity_rejected():
    value = UserRuntimeInput(
        target_position_m=(
            10.0,
            math.inf,
            -3.0,
        ),
        position_tolerance_m=0.5,
        timeout_s=30.0,
    )

    result = make_validator().validate(
        value
    )

    assert result.valid is False


def test_non_positive_tolerance_rejected():
    value = UserRuntimeInput(
        target_position_m=(
            10.0,
            5.0,
            -3.0,
        ),
        position_tolerance_m=0.0,
        timeout_s=30.0,
    )

    result = make_validator().validate(
        value
    )

    assert result.valid is False
    assert (
        "position_tolerance_m_must_be_positive"
        in result.errors
    )


def test_non_positive_timeout_rejected():
    value = UserRuntimeInput(
        target_position_m=(
            10.0,
            5.0,
            -3.0,
        ),
        position_tolerance_m=0.5,
        timeout_s=0.0,
    )

    result = make_validator().validate(
        value
    )

    assert result.valid is False
    assert (
        "timeout_s_must_be_positive"
        in result.errors
    )


def test_unsupported_frame_rejected():
    value = UserRuntimeInput(
        target_position_m=(
            10.0,
            5.0,
            -3.0,
        ),
        position_tolerance_m=0.5,
        timeout_s=30.0,
        frame="GLOBAL",
    )

    result = make_validator().validate(
        value
    )

    assert result.valid is False
    assert (
        "unsupported_frame:GLOBAL"
        in result.errors
    )


def test_target_outside_area_rejected():
    value = UserRuntimeInput(
        target_position_m=(
            100.0,
            5.0,
            -3.0,
        ),
        position_tolerance_m=0.5,
        timeout_s=30.0,
    )

    result = make_validator().validate(
        value
    )

    assert result.valid is False
    assert (
        "target_outside_permitted_test_region"
        in result.errors
    )


def test_missing_test_area_rejected():
    validator = RuntimeInputValidator(
        supported_frames={"LOCAL_NED"},
        test_area=None,
    )

    result = validator.validate(
        make_valid_input()
    )

    assert result.valid is False
    assert (
        "test_area_not_configured"
        in result.errors
    )


def test_system_state_spoof_rejected():
    validator = make_validator()

    result = validator.validate_raw_mapping(
        {
            "target_position_m": [
                10.0,
                5.0,
                -3.0,
            ],
            "position_tolerance_m": 0.5,
            "timeout_s": 30.0,
            "frame": "LOCAL_NED",
            "scenario_id": "T01",

            # User must NOT supply this.
            "localization_valid": True,
        }
    )

    assert result.valid is False

    assert any(
        item.startswith(
            "system_state_spoof:"
        )
        for item in result.errors
    )


def test_missing_required_field_rejected():
    result = (
        make_validator()
        .validate_raw_mapping(
            {
                "target_position_m": [
                    10.0,
                    5.0,
                    -3.0,
                ],
                "timeout_s": 30.0,
            }
        )
    )

    assert result.valid is False

    assert any(
        item.startswith(
            "missing_fields:"
        )
        for item in result.errors
    )


def test_valid_input_generates_artifacts(
    tmp_path: Path,
):
    canonical = build_input_artifacts(
        runtime_input=make_valid_input(),
        validator=make_validator(),
        artifact_dir=tmp_path,
        task_id="NAV-001-T01",
    )

    assert canonical.task_id == (
        "NAV-001-T01"
    )

    assert (
        tmp_path
        / "raw_user_input.json"
    ).exists()

    assert (
        tmp_path
        / "input_validation.json"
    ).exists()

    assert (
        tmp_path
        / "input.json"
    ).exists()

    input_data = json.loads(
        (
            tmp_path
            / "input.json"
        ).read_text()
    )

    assert input_data == {
        "frame": "LOCAL_NED",
        "position_tolerance_m": 0.5,
        "scenario_id": "T01",
        "target_position_m": [
            10.0,
            5.0,
            -3.0,
        ],
        "task_id": "NAV-001-T01",
        "timeout_s": 30.0,
    }


def test_invalid_input_does_not_create_input_json(
    tmp_path: Path,
):
    invalid = UserRuntimeInput(
        target_position_m=(
            100.0,
            0.0,
            -3.0,
        ),
        position_tolerance_m=0.5,
        timeout_s=30.0,
    )

    with pytest.raises(
        InputBuildError
    ):
        build_input_artifacts(
            runtime_input=invalid,
            validator=make_validator(),
            artifact_dir=tmp_path,
            task_id="NAV-001-T01",
        )

    assert (
        tmp_path
        / "raw_user_input.json"
    ).exists()

    assert (
        tmp_path
        / "input_validation.json"
    ).exists()

    assert not (
        tmp_path
        / "input.json"
    ).exists()
