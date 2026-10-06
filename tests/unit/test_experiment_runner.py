import asyncio
import json
from pathlib import Path

import pytest

from orchestration.runner import (
    ExperimentRunner,
)
from src.core.base_capability import (
    BaseCapabilityModule,
)
from src.core.errors import (
    PreconditionFailedError,
)
from src.evidence.attempt import (
    AttemptEvidence,
)


class DummyExperimentCapability(
    BaseCapabilityModule
):
    def __init__(
        self,
        *,
        fail_precondition=False,
    ):
        super().__init__()

        self.fail_precondition = (
            fail_precondition
        )

    async def validate_input(
        self,
        input_data,
    ):
        if (
            "target_position_m"
            not in input_data
        ):
            raise ValueError(
                "target missing"
            )

    async def check_preconditions(
        self,
        input_data,
    ):
        if self.fail_precondition:
            raise PreconditionFailedError(
                "dummy precondition failed"
            )

    async def execute(
        self,
        input_data,
    ):
        return {
            "received_target":
                input_data[
                    "target_position_m"
                ]
        }

    async def build_result(
        self,
        input_data,
        execution_output,
        elapsed_time_s,
    ):
        return execution_output


def make_attempt(
    tmp_path: Path,
) -> AttemptEvidence:
    evidence = AttemptEvidence(
        root=tmp_path / "runs",
        task_id="NAV-001",
        scenario_id="T01",
        attempt=1,
    )

    evidence.create()

    evidence.write_json(
        "input.json",
        {
            "task_id": "NAV-001",
            "scenario_id": "T01",
            "frame": "LOCAL_NED",
            "target_position_m": [
                10.0,
                5.0,
                -3.0,
            ],
            "position_tolerance_m": 0.5,
            "timeout_s": 30.0,
        },
    )

    return evidence


def test_runner_executes_canonical_input(
    tmp_path: Path,
):
    evidence = make_attempt(
        tmp_path
    )

    runner = ExperimentRunner(
        attempt_dir=evidence.path,
        capability=(
            DummyExperimentCapability()
        ),
    )

    result = asyncio.run(
        runner.run()
    )

    assert (
        result.module_result
        .status_value
        == "completed"
    )

    output = json.loads(
        (
            evidence.path
            / "module_output.json"
        ).read_text()
    )

    assert (
        output["data"]
        ["received_target"]
        == [
            10.0,
            5.0,
            -3.0,
        ]
    )


def test_runner_records_lifecycle(
    tmp_path: Path,
):
    evidence = make_attempt(
        tmp_path
    )

    runner = ExperimentRunner(
        attempt_dir=evidence.path,
        capability=(
            DummyExperimentCapability()
        ),
    )

    result = asyncio.run(
        runner.run()
    )

    assert (
        result.module_result.lifecycle
        == (
            "CREATED",
            "VALIDATE_INPUT",
            "CHECK_PRECONDITIONS",
            "EXECUTE",
            "COLLECT_RESULT",
            "FINISHED",
        )
    )


def test_runner_events_created(
    tmp_path: Path,
):
    evidence = make_attempt(
        tmp_path
    )

    runner = ExperimentRunner(
        attempt_dir=evidence.path,
        capability=(
            DummyExperimentCapability()
        ),
    )

    asyncio.run(
        runner.run()
    )

    events = (
        evidence.path
        / "events.jsonl"
    ).read_text()

    assert (
        "experiment_started"
        in events
    )

    assert (
        "experiment_finished"
        in events
    )


def test_precondition_failure_is_recorded(
    tmp_path: Path,
):
    evidence = make_attempt(
        tmp_path
    )

    runner = ExperimentRunner(
        attempt_dir=evidence.path,
        capability=(
            DummyExperimentCapability(
                fail_precondition=True
            )
        ),
    )

    result = asyncio.run(
        runner.run()
    )

    assert (
        result.module_result
        .status_value
        == "failed"
    )

    assert (
        result.module_result
        .error_code
        == "PRECONDITION_FAILED"
    )

    output = json.loads(
        (
            evidence.path
            / "module_output.json"
        ).read_text()
    )

    assert (
        output["error_code"]
        == "PRECONDITION_FAILED"
    )


def test_runner_refuses_missing_input(
    tmp_path: Path,
):
    evidence = AttemptEvidence(
        root=tmp_path / "runs",
        task_id="NAV-001",
        scenario_id="T01",
        attempt=1,
    )

    evidence.create()

    runner = ExperimentRunner(
        attempt_dir=evidence.path,
        capability=(
            DummyExperimentCapability()
        ),
    )

    with pytest.raises(
        FileNotFoundError
    ):
        asyncio.run(
            runner.run()
        )


def test_runner_refuses_finalized_attempt(
    tmp_path: Path,
):
    evidence = make_attempt(
        tmp_path
    )

    evidence.finalize()

    runner = ExperimentRunner(
        attempt_dir=evidence.path,
        capability=(
            DummyExperimentCapability()
        ),
    )

    with pytest.raises(
        RuntimeError
    ):
        asyncio.run(
            runner.run()
        )


def test_runner_refuses_same_attempt_rerun(
    tmp_path: Path,
):
    evidence = make_attempt(
        tmp_path
    )

    first = ExperimentRunner(
        attempt_dir=evidence.path,
        capability=(
            DummyExperimentCapability()
        ),
    )

    asyncio.run(
        first.run()
    )

    second = ExperimentRunner(
        attempt_dir=evidence.path,
        capability=(
            DummyExperimentCapability()
        ),
    )

    with pytest.raises(
        FileExistsError
    ):
        asyncio.run(
            second.run()
        )
