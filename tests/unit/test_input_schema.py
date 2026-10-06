from user_interface.input_schema import (
    CANONICAL_INPUT_FIELDS,
    SYSTEM_OBSERVED_FIELDS,
    USER_PROVIDED_FIELDS,
    CanonicalExperimentInput,
    UserRuntimeInput,
)


def test_user_runtime_input_serializes():
    value = UserRuntimeInput(
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

    assert value.to_dict() == {
        "target_position_m": [
            10.0,
            5.0,
            -3.0,
        ],
        "position_tolerance_m": 0.5,
        "timeout_s": 30.0,
        "frame": "LOCAL_NED",
        "scenario_id": "T01",
    }


def test_canonical_schema_matches_plan():
    assert CANONICAL_INPUT_FIELDS == {
        "task_id",
        "frame",
        "target_position_m",
        "position_tolerance_m",
        "timeout_s",
        "scenario_id",
    }


def test_system_state_not_user_fields():
    assert (
        USER_PROVIDED_FIELDS
        & SYSTEM_OBSERVED_FIELDS
    ) == set()

    assert "current_position" not in (
        USER_PROVIDED_FIELDS
    )

    assert "airborne_state" not in (
        USER_PROVIDED_FIELDS
    )

    assert "localization_valid" not in (
        USER_PROVIDED_FIELDS
    )


def test_canonical_input_serializes():
    value = CanonicalExperimentInput(
        task_id="NAV-001-T01",
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

    result = value.to_dict()

    assert result["task_id"] == (
        "NAV-001-T01"
    )

    assert result[
        "target_position_m"
    ] == [
        10.0,
        5.0,
        -3.0,
    ]

    assert (
        "current_position"
        not in result
    )

    assert (
        "localization_valid"
        not in result
    )
