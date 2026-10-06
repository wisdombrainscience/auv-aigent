import json

import pytest

from user_interface.cli import (
    main,
    parse_runtime_input,
)


def test_parse_required_cli_values():
    value = parse_runtime_input(
        [
            "--target-x",
            "10",
            "--target-y",
            "5",
            "--target-z",
            "-3",
            "--tolerance",
            "0.5",
            "--timeout",
            "30",
        ]
    )

    assert value.target_position_m == (
        10.0,
        5.0,
        -3.0,
    )

    assert value.position_tolerance_m == 0.5
    assert value.timeout_s == 30.0
    assert value.frame == "LOCAL_NED"
    assert value.scenario_id == "T01"


def test_parse_optional_cli_values():
    value = parse_runtime_input(
        [
            "--target-x",
            "1",
            "--target-y",
            "2",
            "--target-z",
            "-4",
            "--tolerance",
            "0.25",
            "--timeout",
            "12",
            "--frame",
            "LOCAL_NED",
            "--scenario-id",
            "T99",
        ]
    )

    assert value.frame == "LOCAL_NED"
    assert value.scenario_id == "T99"


def test_cli_prints_json(capsys):
    rc = main(
        [
            "--target-x",
            "10",
            "--target-y",
            "5",
            "--target-z",
            "-3",
            "--tolerance",
            "0.5",
            "--timeout",
            "30",
        ]
    )

    assert rc == 0

    output = capsys.readouterr().out

    data = json.loads(output)

    assert data["target_position_m"] == [
        10.0,
        5.0,
        -3.0,
    ]

    assert data["frame"] == "LOCAL_NED"

    assert "current_position" not in data
    assert "airborne_state" not in data
    assert "localization_valid" not in data


def test_missing_required_argument_fails():
    with pytest.raises(SystemExit):
        parse_runtime_input(
            [
                "--target-x",
                "10",
            ]
        )


def test_non_numeric_target_fails():
    with pytest.raises(SystemExit):
        parse_runtime_input(
            [
                "--target-x",
                "abc",
                "--target-y",
                "5",
                "--target-z",
                "-3",
                "--tolerance",
                "0.5",
                "--timeout",
                "30",
            ]
        )
