from __future__ import annotations

import asyncio
import math
import time
from abc import ABC, abstractmethod
from enum import Enum
from typing import Any, Callable

from src.core.errors import (
    CapabilityError,
    FailureCode,
    InvalidInputError,
)
from src.core.result import (
    CapabilityResult,
    CapabilityStatus,
)


class LifecycleState(str, Enum):
    CREATED = "CREATED"
    VALIDATE_INPUT = "VALIDATE_INPUT"
    CHECK_PRECONDITIONS = "CHECK_PRECONDITIONS"
    EXECUTE = "EXECUTE"
    COLLECT_RESULT = "COLLECT_RESULT"
    FINISHED = "FINISHED"


LogHook = Callable[
    [LifecycleState, dict[str, Any]],
    None,
]


class BaseCapabilityModule(ABC):
    """
    Shared deterministic lifecycle for UAV capabilities.

    Official verification does NOT happen here.
    A capability can report completion, but the
    independent verifier owns PASS / FAIL.
    """

    def __init__(
        self,
        log_hook: LogHook | None = None,
    ) -> None:
        self.state = LifecycleState.CREATED

        self.lifecycle_history: list[
            LifecycleState
        ] = [
            LifecycleState.CREATED
        ]

        self.log_hook = log_hook

    def _transition(
        self,
        state: LifecycleState,
        **context: Any,
    ) -> None:
        self.state = state
        self.lifecycle_history.append(state)

        if self.log_hook is not None:
            self.log_hook(
                state,
                context,
            )

    @staticmethod
    def _timeout_from_input(
        input_data: dict[str, Any],
    ) -> float:
        if "timeout_s" not in input_data:
            raise InvalidInputError(
                "timeout_s is required"
            )

        value = input_data["timeout_s"]

        if isinstance(value, bool):
            raise InvalidInputError(
                "timeout_s must be numeric"
            )

        try:
            timeout = float(value)
        except (TypeError, ValueError) as exc:
            raise InvalidInputError(
                "timeout_s must be numeric"
            ) from exc

        if not math.isfinite(timeout):
            raise InvalidInputError(
                "timeout_s must be finite"
            )

        if timeout <= 0:
            raise InvalidInputError(
                "timeout_s must be > 0"
            )

        return timeout

    @abstractmethod
    async def validate_input(
        self,
        input_data: dict[str, Any],
    ) -> None:
        ...

    @abstractmethod
    async def check_preconditions(
        self,
        input_data: dict[str, Any],
    ) -> None:
        ...

    @abstractmethod
    async def execute(
        self,
        input_data: dict[str, Any],
    ) -> Any:
        ...

    @abstractmethod
    async def build_result(
        self,
        input_data: dict[str, Any],
        execution_output: Any,
        elapsed_time_s: float,
    ) -> dict[str, Any]:
        ...

    async def run(
        self,
        input_data: dict[str, Any],
    ) -> CapabilityResult:
        started = time.monotonic()

        try:
            self._transition(
                LifecycleState.VALIDATE_INPUT
            )

            await self.validate_input(
                input_data
            )

            timeout_s = (
                self._timeout_from_input(
                    input_data
                )
            )

            self._transition(
                LifecycleState.CHECK_PRECONDITIONS
            )

            await self.check_preconditions(
                input_data
            )

            self._transition(
                LifecycleState.EXECUTE
            )

            execution_output = (
                await asyncio.wait_for(
                    self.execute(input_data),
                    timeout=timeout_s,
                )
            )

            elapsed = (
                time.monotonic()
                - started
            )

            self._transition(
                LifecycleState.COLLECT_RESULT
            )

            data = await self.build_result(
                input_data,
                execution_output,
                elapsed,
            )

            result = CapabilityResult(
                status=(
                    CapabilityStatus.COMPLETED
                ),
                elapsed_time_s=elapsed,
                data=data,
                lifecycle=(),
            )

        except asyncio.TimeoutError:
            elapsed = (
                time.monotonic()
                - started
            )

            if (
                self.state
                != LifecycleState.COLLECT_RESULT
            ):
                self._transition(
                    LifecycleState.COLLECT_RESULT
                )

            result = CapabilityResult(
                status=CapabilityStatus.FAILED,
                elapsed_time_s=elapsed,
                error_code=(
                    FailureCode.TIMEOUT.value
                ),
                reason=(
                    "Capability execution "
                    "exceeded timeout"
                ),
                lifecycle=(),
            )

        except CapabilityError as exc:
            elapsed = (
                time.monotonic()
                - started
            )

            if (
                self.state
                != LifecycleState.COLLECT_RESULT
            ):
                self._transition(
                    LifecycleState.COLLECT_RESULT
                )

            result = CapabilityResult(
                status=CapabilityStatus.FAILED,
                elapsed_time_s=elapsed,
                error_code=exc.code.value,
                reason=exc.reason,
                lifecycle=(),
            )

        except Exception as exc:
            elapsed = (
                time.monotonic()
                - started
            )

            if (
                self.state
                != LifecycleState.COLLECT_RESULT
            ):
                self._transition(
                    LifecycleState.COLLECT_RESULT
                )

            result = CapabilityResult(
                status=CapabilityStatus.FAILED,
                elapsed_time_s=elapsed,
                error_code=(
                    FailureCode.EXECUTION_ERROR.value
                ),
                reason=str(exc),
                lifecycle=(),
            )

        self._transition(
            LifecycleState.FINISHED
        )

        return CapabilityResult(
            status=result.status,
            elapsed_time_s=result.elapsed_time_s,
            data=result.data,
            error_code=result.error_code,
            reason=result.reason,
            lifecycle=tuple(
                state.value
                for state
                in self.lifecycle_history
            ),
        )
