from enum import Enum


class FailureCode(str, Enum):
    INVALID_INPUT = "INVALID_INPUT"
    PRECONDITION_FAILED = "PRECONDITION_FAILED"
    UNSUPPORTED_FRAME = "UNSUPPORTED_FRAME"
    LOCALIZATION_LOST = "LOCALIZATION_LOST"
    TIMEOUT = "TIMEOUT"
    EXECUTION_ERROR = "EXECUTION_ERROR"
    TARGET_NOT_REACHED = "TARGET_NOT_REACHED"
    VERIFICATION_FAILED = "VERIFICATION_FAILED"
    BOUNDARY_VIOLATION = "BOUNDARY_VIOLATION"
    AGENT_JOB_FAILED = "AGENT_JOB_FAILED"


class CapabilityError(RuntimeError):
    def __init__(
        self,
        code: FailureCode,
        reason: str,
    ) -> None:
        super().__init__(reason)
        self.code = code
        self.reason = reason


class InvalidInputError(CapabilityError):
    def __init__(self, reason: str) -> None:
        super().__init__(
            FailureCode.INVALID_INPUT,
            reason,
        )


class PreconditionFailedError(CapabilityError):
    def __init__(self, reason: str) -> None:
        super().__init__(
            FailureCode.PRECONDITION_FAILED,
            reason,
        )


class UnsupportedFrameError(CapabilityError):
    def __init__(self, reason: str) -> None:
        super().__init__(
            FailureCode.UNSUPPORTED_FRAME,
            reason,
        )


class LocalizationLostError(CapabilityError):
    def __init__(self, reason: str) -> None:
        super().__init__(
            FailureCode.LOCALIZATION_LOST,
            reason,
        )
