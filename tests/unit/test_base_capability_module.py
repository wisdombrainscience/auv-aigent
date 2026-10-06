import asyncio

from src.core.base_capability import (
    BaseCapabilityModule,
    LifecycleState,
)
from src.core.errors import (
    InvalidInputError,
    PreconditionFailedError,
)
from src.core.result import CapabilityStatus


class DummyCapability(
    BaseCapabilityModule
):
    def __init__(
        self,
        *,
        invalid=False,
        precondition_fail=False,
        sleep_s=0.0,
        log_hook=None,
    ):
        super().__init__(
            log_hook=log_hook
        )

        self.invalid = invalid
        self.precondition_fail = (
            precondition_fail
        )
        self.sleep_s = sleep_s
        self.execute_called = False

    async def validate_input(
        self,
        input_data,
    ):
        if self.invalid:
            raise InvalidInputError(
                "invalid test input"
            )

    async def check_preconditions(
        self,
        input_data,
    ):
        if self.precondition_fail:
            raise PreconditionFailedError(
                "vehicle not ready"
            )

    async def execute(
        self,
        input_data,
    ):
        self.execute_called = True

        if self.sleep_s:
            await asyncio.sleep(
                self.sleep_s
            )

        return {"execution": "ok"}

    async def build_result(
        self,
        input_data,
        execution_output,
        elapsed_time_s,
    ):
        return {
            "execution_output":
                execution_output
        }


def run(coro):
    return asyncio.run(coro)


def test_success_lifecycle():
    module = DummyCapability()

    result = run(
        module.run(
            {"timeout_s": 1.0}
        )
    )

    assert (
        result.status
        == CapabilityStatus.COMPLETED
    )

    assert result.lifecycle == (
        "CREATED",
        "VALIDATE_INPUT",
        "CHECK_PRECONDITIONS",
        "EXECUTE",
        "COLLECT_RESULT",
        "FINISHED",
    )


def test_invalid_input():
    module = DummyCapability(
        invalid=True
    )

    result = run(
        module.run(
            {"timeout_s": 1.0}
        )
    )

    assert result.status == (
        CapabilityStatus.FAILED
    )

    assert (
        result.error_code
        == "INVALID_INPUT"
    )

    assert module.execute_called is False


def test_precondition_failure():
    module = DummyCapability(
        precondition_fail=True
    )

    result = run(
        module.run(
            {"timeout_s": 1.0}
        )
    )

    assert result.status == (
        CapabilityStatus.FAILED
    )

    assert (
        result.error_code
        == "PRECONDITION_FAILED"
    )

    assert module.execute_called is False


def test_execution_timeout():
    module = DummyCapability(
        sleep_s=0.2
    )

    result = run(
        module.run(
            {"timeout_s": 0.05}
        )
    )

    assert result.status == (
        CapabilityStatus.FAILED
    )

    assert (
        result.error_code
        == "TIMEOUT"
    )


def test_logging_hook_receives_states():
    observed = []

    def hook(state, context):
        observed.append(state)

    module = DummyCapability(
        log_hook=hook
    )

    result = run(
        module.run(
            {"timeout_s": 1.0}
        )
    )

    assert result.status == (
        CapabilityStatus.COMPLETED
    )

    assert observed == [
        LifecycleState.VALIDATE_INPUT,
        LifecycleState.CHECK_PRECONDITIONS,
        LifecycleState.EXECUTE,
        LifecycleState.COLLECT_RESULT,
        LifecycleState.FINISHED,
    ]
