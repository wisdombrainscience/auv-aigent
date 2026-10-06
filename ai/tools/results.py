from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import math
from types import MappingProxyType
from typing import Any, Callable, Generic, Mapping, TypeAlias, TypeVar

from ai.contracts.failures import (
    FailureClass,
    FailureDescriptor,
    parse_failure_descriptor,
)
from ai.tools.allowlists import RoleToolAllowlistError, ToolNotAllowedError
from ai.tools.paths import (
    ToolPathDeniedError,
    ToolPathNotApplicableError,
    ToolPathRestrictionError,
)
from ai.tools.timeout import (
    ToolInvocationError,
    ToolTimedOutError,
    ToolTimeoutPolicyError,
    run_tool_with_timeout,
)


TOOL_RESULT_CONTRACT_VERSION = "1.0"

T = TypeVar("T")


class ToolResultError(ValueError):
    """Raised when an X10.5 structured tool-result envelope is invalid."""


class ToolResultStatus(str, Enum):
    SUCCESS = "SUCCESS"
    FAILURE = "FAILURE"


JSONScalar: TypeAlias = None | bool | int | float | str
JSONValue: TypeAlias = JSONScalar | tuple["JSONValue", ...] | Mapping[str, "JSONValue"]

_TOOL_RESULT_KEYS = frozenset({"status", "role_id", "tool_id", "output", "failure"})


def _clean_text(value: Any, *, field: str) -> str:
    if not isinstance(value, str):
        raise ToolResultError(f"{field} must be a string")
    if not value:
        raise ToolResultError(f"{field} must not be empty")
    if value != value.strip() or any(ch.isspace() for ch in value):
        raise ToolResultError(f"{field} must not contain whitespace")
    return value


def _parse_status(raw: Any) -> ToolResultStatus:
    if isinstance(raw, ToolResultStatus):
        return raw
    if not isinstance(raw, str):
        raise ToolResultError("status must be a string or ToolResultStatus")
    if raw != raw.strip() or any(ch.isspace() for ch in raw):
        raise ToolResultError("status must not contain whitespace")
    try:
        return ToolResultStatus(raw)
    except ValueError as exc:
        raise ToolResultError("status must be exactly SUCCESS or FAILURE") from exc


def _freeze_json(value: Any, *, path: str) -> JSONValue:
    if value is None or isinstance(value, (bool, str)):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ToolResultError(f"{path} contains a non-finite number")
        return value
    if isinstance(value, (list, tuple)):
        return tuple(
            _freeze_json(item, path=f"{path}[{index}]")
            for index, item in enumerate(value)
        )
    if isinstance(value, Mapping):
        frozen: dict[str, JSONValue] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise ToolResultError(f"{path} contains a non-string object key")
            if not key:
                raise ToolResultError(f"{path} contains an empty object key")
            frozen[key] = _freeze_json(item, path=f"{path}.{key}")
        return MappingProxyType(frozen)
    raise ToolResultError(
        f"{path} contains unsupported JSON value type {type(value).__name__}"
    )


def _thaw_json(value: JSONValue) -> Any:
    if isinstance(value, tuple):
        return [_thaw_json(item) for item in value]
    if isinstance(value, Mapping):
        return {key: _thaw_json(item) for key, item in value.items()}
    return value


def _tool_failure(
    *,
    role_id: str,
    tool_id: str,
    code: str,
    message: str,
    details: Mapping[str, Any] | None = None,
) -> FailureDescriptor:
    merged: dict[str, Any] = {
        "role_id": role_id,
        "tool_id": tool_id,
    }
    if details:
        for key, value in details.items():
            if key in merged:
                raise ToolResultError(f"reserved tool failure detail key: {key}")
            merged[key] = value
    return FailureDescriptor.from_dict(
        {
            "class": FailureClass.TOOL.value,
            "code": code,
            "message": message,
            "details": merged,
        }
    )


@dataclass(frozen=True)
class ToolResult(Generic[T]):
    """Frozen X10.5 tool-result envelope.

    The envelope is deliberately transport/data only. It does not persist an
    audit record, retry a tool, execute a command, open a path, or authorize a
    role. X10.6 is solely responsible for audit logging.
    """

    status: ToolResultStatus
    role_id: str
    tool_id: str
    output: JSONValue
    failure: FailureDescriptor | None

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "ToolResult[Any]":
        if not isinstance(raw, Mapping):
            raise ToolResultError("tool result payload must be an object")
        keys = frozenset(raw.keys())
        if keys != _TOOL_RESULT_KEYS:
            missing = sorted(_TOOL_RESULT_KEYS - keys)
            extra = sorted(keys - _TOOL_RESULT_KEYS)
            parts: list[str] = []
            if missing:
                parts.append("missing=" + ",".join(missing))
            if extra:
                parts.append("extra=" + ",".join(str(item) for item in extra))
            raise ToolResultError(
                "tool result payload must contain exactly the X10.5 fields"
                + (" (" + "; ".join(parts) + ")" if parts else "")
            )

        status = _parse_status(raw["status"])
        role_id = _clean_text(raw["role_id"], field="role_id")
        tool_id = _clean_text(raw["tool_id"], field="tool_id")
        output = _freeze_json(raw["output"], path="output")

        raw_failure = raw["failure"]
        failure: FailureDescriptor | None
        if raw_failure is None:
            failure = None
        else:
            if isinstance(raw_failure, FailureDescriptor):
                failure = raw_failure
            elif isinstance(raw_failure, Mapping):
                failure = parse_failure_descriptor(raw_failure)
            else:
                raise ToolResultError("failure must be a FailureDescriptor object or null")
            if failure.failure_class is not FailureClass.TOOL:
                raise ToolResultError("tool result failure must use TOOL failure class")

        if status is ToolResultStatus.SUCCESS:
            if failure is not None:
                raise ToolResultError("SUCCESS tool result must have failure=null")
        else:
            if failure is None:
                raise ToolResultError("FAILURE tool result must include failure")
            if output is not None:
                raise ToolResultError("FAILURE tool result must have output=null")

        return cls(
            status=status,
            role_id=role_id,
            tool_id=tool_id,
            output=output,
            failure=failure,
        )

    @classmethod
    def success(cls, *, role_id: str, tool_id: str, output: Any) -> "ToolResult[Any]":
        return cls.from_dict(
            {
                "status": ToolResultStatus.SUCCESS.value,
                "role_id": role_id,
                "tool_id": tool_id,
                "output": output,
                "failure": None,
            }
        )

    @classmethod
    def failed(
        cls,
        *,
        role_id: str,
        tool_id: str,
        failure: FailureDescriptor,
    ) -> "ToolResult[Any]":
        if not isinstance(failure, FailureDescriptor):
            raise ToolResultError("failure must be FailureDescriptor")
        return cls.from_dict(
            {
                "status": ToolResultStatus.FAILURE.value,
                "role_id": role_id,
                "tool_id": tool_id,
                "output": None,
                "failure": failure,
            }
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "role_id": self.role_id,
            "tool_id": self.tool_id,
            "output": _thaw_json(self.output),
            "failure": None if self.failure is None else self.failure.to_dict(),
        }


def parse_tool_result(raw: Mapping[str, Any]) -> ToolResult[Any]:
    return ToolResult.from_dict(raw)


def tool_failure_from_exception(
    *,
    role_id: str,
    tool_id: str,
    error: BaseException,
) -> ToolResult[Any]:
    """Normalize one known X10.x operational failure into the X10.5 envelope.

    Unknown programming errors are deliberately not swallowed. This converter
    recognizes only bounded tool-policy/runtime failures frozen through X10.5.
    """

    canonical_role = _clean_text(role_id, field="role_id")
    canonical_tool = _clean_text(tool_id, field="tool_id")

    if isinstance(error, ToolTimedOutError):
        return ToolResult.failed(
            role_id=canonical_role,
            tool_id=canonical_tool,
            failure=error.failure,
        )
    if isinstance(error, ToolNotAllowedError):
        failure = _tool_failure(
            role_id=canonical_role,
            tool_id=canonical_tool,
            code="TOOL_NOT_ALLOWED",
            message="Tool is not allowlisted for the requested role.",
            details={"reason": "NOT_ALLOWLISTED"},
        )
    elif isinstance(error, RoleToolAllowlistError):
        failure = _tool_failure(
            role_id=canonical_role,
            tool_id=canonical_tool,
            code="TOOL_NOT_ALLOWED",
            message="Tool authorization failed closed.",
            details={"reason": "UNKNOWN_OR_INVALID_ROLE_TOOL"},
        )
    elif isinstance(error, ToolTimeoutPolicyError):
        failure = _tool_failure(
            role_id=canonical_role,
            tool_id=canonical_tool,
            code="TOOL_POLICY_INVALID",
            message="Tool timeout policy is invalid.",
        )
    elif isinstance(error, ToolInvocationError):
        failure = _tool_failure(
            role_id=canonical_role,
            tool_id=canonical_tool,
            code="TOOL_INVOCATION_FAILED",
            message="Tool invocation failed before a structured result was produced.",
        )
    elif isinstance(error, ToolPathNotApplicableError):
        failure = _tool_failure(
            role_id=canonical_role,
            tool_id=canonical_tool,
            code="TOOL_PATH_NOT_APPLICABLE",
            message="Tool does not expose a filesystem path interface.",
        )
    elif isinstance(error, ToolPathDeniedError):
        failure = _tool_failure(
            role_id=canonical_role,
            tool_id=canonical_tool,
            code="TOOL_PATH_DENIED",
            message="Tool path request was denied by the sandbox policy.",
        )
    elif isinstance(error, ToolPathRestrictionError):
        failure = _tool_failure(
            role_id=canonical_role,
            tool_id=canonical_tool,
            code="TOOL_PATH_INVALID",
            message="Tool path request violated the path-restriction contract.",
        )
    else:
        raise TypeError(f"unsupported tool operational error: {type(error).__name__}")

    return ToolResult.failed(
        role_id=canonical_role,
        tool_id=canonical_tool,
        failure=failure,
    )


def run_tool_structured(
    *,
    role_id: str,
    tool_id: str,
    operation: Callable[[], T],
    timeout_s: float,
) -> ToolResult[T]:
    """Run one X10.3 bounded trusted callable and return only X10.5 data.

    This API intentionally has no command, argv, shell, URL, path, provider,
    PX4, MAVSDK, persistence, or audit argument. Path-bearing tools must be
    authorized/resolved through X10.4 before trusted application code binds the
    callable. X10.6 will add audit logging around this result contract.
    """

    canonical_role = _clean_text(role_id, field="role_id")
    canonical_tool = _clean_text(tool_id, field="tool_id")
    try:
        value = run_tool_with_timeout(
            role_id=canonical_role,
            tool_id=canonical_tool,
            operation=operation,
            timeout_s=timeout_s,
        )
    except (
        ToolTimedOutError,
        ToolNotAllowedError,
        RoleToolAllowlistError,
        ToolTimeoutPolicyError,
        ToolInvocationError,
    ) as exc:
        return tool_failure_from_exception(
            role_id=canonical_role,
            tool_id=canonical_tool,
            error=exc,
        )

    try:
        return ToolResult.success(
            role_id=canonical_role,
            tool_id=canonical_tool,
            output=value,
        )
    except ToolResultError:
        failure = _tool_failure(
            role_id=canonical_role,
            tool_id=canonical_tool,
            code="TOOL_RESULT_INVALID",
            message="Tool output is not strict JSON-compatible structured data.",
        )
        return ToolResult.failed(
            role_id=canonical_role,
            tool_id=canonical_tool,
            failure=failure,
        )
