from __future__ import annotations

from dataclasses import dataclass
import math
import multiprocessing
from multiprocessing.connection import Connection
from typing import Any, Callable, Generic, TypeVar

from ai.contracts.failures import FailureDescriptor
from ai.tasks.lifecycle import TASK_TIMEOUT_S
from ai.tools.allowlists import (
    DEFAULT_ROLE_TOOL_ALLOWLIST_REGISTRY,
    RoleToolAllowlistRegistry,
)


TOOL_TIMEOUT_CONTRACT_VERSION = "1.0"
MAX_TOOL_TIMEOUT_S = float(TASK_TIMEOUT_S)

T = TypeVar("T")


class ToolTimeoutPolicyError(ValueError):
    """Raised when an X10.3 timeout policy is invalid."""


class ToolInvocationError(RuntimeError):
    """Raised when a bounded child process fails before producing a result."""


class ToolTimedOutError(TimeoutError):
    """Raised after a bounded tool call is forcibly stopped at its deadline."""

    def __init__(self, failure: FailureDescriptor) -> None:
        self.failure = failure
        super().__init__(failure.message)


@dataclass(frozen=True)
class ToolTimeoutPolicy:
    """Finite timeout policy for one trusted logical tool invocation.

    X10.3 deliberately does not define filesystem policy, structured tool
    results, audit persistence, an executor registry, a subprocess command API,
    shell access, network access, provider access, or UAV/PX4 access. Those
    responsibilities remain outside this gate.
    """

    timeout_s: float

    def __post_init__(self) -> None:
        raw = self.timeout_s
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            raise ToolTimeoutPolicyError("timeout_s must be a finite number")
        value = float(raw)
        if not math.isfinite(value):
            raise ToolTimeoutPolicyError("timeout_s must be finite")
        if value <= 0.0:
            raise ToolTimeoutPolicyError("timeout_s must be greater than zero")
        if value > MAX_TOOL_TIMEOUT_S:
            raise ToolTimeoutPolicyError(
                f"timeout_s must not exceed MAX_TOOL_TIMEOUT_S={MAX_TOOL_TIMEOUT_S}"
            )
        object.__setattr__(self, "timeout_s", value)


def _child_entry(send_conn: Connection, operation: Callable[[], Any]) -> None:
    try:
        try:
            value = operation()
        except BaseException as exc:  # Child exception must not escape silently.
            try:
                send_conn.send(("error", type(exc).__name__, str(exc)))
            except BaseException:
                pass
            return

        try:
            send_conn.send(("ok", value))
        except BaseException as exc:
            try:
                send_conn.send(("transport_error", type(exc).__name__, str(exc)))
            except BaseException:
                pass
    finally:
        send_conn.close()


def _timeout_failure(*, role_id: str, tool_id: str, timeout_s: float) -> FailureDescriptor:
    return FailureDescriptor.from_dict(
        {
            "class": "TOOL",
            "code": "TOOL_TIMEOUT",
            "message": "Tool execution exceeded its bounded timeout.",
            "details": {
                "role_id": role_id,
                "tool_id": tool_id,
                "timeout_s": timeout_s,
            },
        }
    )


class ToolTimeoutWrapper(Generic[T]):
    """Run one trusted callable behind X10.2 authorization and a hard timeout.

    The callable is supplied by trusted application code, not by model output,
    and must be pickleable/importable for the spawn process boundary. X10.3
    exposes no command-string, argv, shell, path, URL, provider, PX4, or
    MAVSDK interface. A daemon child process provides a killable execution
    boundary; later gates add path restrictions, structured results, and audit.
    """

    def __init__(
        self,
        *,
        allowlists: RoleToolAllowlistRegistry = DEFAULT_ROLE_TOOL_ALLOWLIST_REGISTRY,
    ) -> None:
        self._allowlists = allowlists

    def run(
        self,
        *,
        role_id: str,
        tool_id: str,
        operation: Callable[[], T],
        policy: ToolTimeoutPolicy,
    ) -> T:
        canonical_tool = self._allowlists.require_allowed(role_id, tool_id)
        canonical_role = self._allowlists.get(role_id).role_id
        if not isinstance(policy, ToolTimeoutPolicy):
            raise ToolTimeoutPolicyError("policy must be ToolTimeoutPolicy")
        if not callable(operation):
            raise TypeError("operation must be callable")

        try:
            ctx = multiprocessing.get_context("spawn")
        except ValueError as exc:
            raise ToolInvocationError("spawn multiprocessing context is unavailable") from exc

        recv_conn, send_conn = ctx.Pipe(duplex=False)
        process = ctx.Process(
            target=_child_entry,
            args=(send_conn, operation),
            name=f"ruav-tool-{canonical_tool}",
            daemon=True,
        )

        process.start()
        send_conn.close()
        try:
            process.join(policy.timeout_s)
            if process.is_alive():
                process.terminate()
                process.join(1.0)
                if process.is_alive():
                    process.kill()
                    process.join(1.0)
                raise ToolTimedOutError(
                    _timeout_failure(
                        role_id=canonical_role,
                        tool_id=canonical_tool,
                        timeout_s=policy.timeout_s,
                    )
                )

            if not recv_conn.poll():
                raise ToolInvocationError(
                    f"tool child exited without result: tool={canonical_tool} "
                    f"exitcode={process.exitcode}"
                )

            message = recv_conn.recv()
        finally:
            recv_conn.close()
            if process.is_alive():
                process.terminate()
                process.join(1.0)
                if process.is_alive():
                    process.kill()
                    process.join(1.0)

        if not isinstance(message, tuple) or not message:
            raise ToolInvocationError("tool child returned invalid transport message")
        kind = message[0]
        if kind == "ok" and len(message) == 2:
            return message[1]
        if kind in {"error", "transport_error"} and len(message) == 3:
            error_type, error_message = message[1], message[2]
            raise ToolInvocationError(
                f"tool child {kind}: type={error_type} message={error_message}"
            )
        raise ToolInvocationError("tool child returned unknown transport message")


def run_tool_with_timeout(
    *,
    role_id: str,
    tool_id: str,
    operation: Callable[[], T],
    timeout_s: float,
    allowlists: RoleToolAllowlistRegistry = DEFAULT_ROLE_TOOL_ALLOWLIST_REGISTRY,
) -> T:
    """Convenience API for one bounded trusted tool callable."""
    return ToolTimeoutWrapper[T](allowlists=allowlists).run(
        role_id=role_id,
        tool_id=tool_id,
        operation=operation,
        policy=ToolTimeoutPolicy(timeout_s),
    )
