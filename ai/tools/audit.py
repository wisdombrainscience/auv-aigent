from __future__ import annotations

from dataclasses import dataclass
import fcntl
import json
import os
from pathlib import Path
from typing import Any, Callable, Generic, Mapping, TypeVar

from ai.project.store import ProjectHandle
from ai.tools.results import ToolResult, ToolResultError, run_tool_structured


TOOL_AUDIT_CONTRACT_VERSION = "1.0"
TOOL_AUDIT_FILENAME = "tool_audit.jsonl"
MAX_TOOL_AUDIT_RECORD_BYTES = 64 * 1024

T = TypeVar("T")


class ToolAuditError(RuntimeError):
    """Raised when the X10.6 project-local tool audit trail is invalid."""


@dataclass(frozen=True)
class ToolAuditRecord(Generic[T]):
    sequence: int
    project_id: str
    role_id: str
    tool_id: str
    result: ToolResult[T]

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "ToolAuditRecord[Any]":
        if not isinstance(raw, Mapping):
            raise ToolAuditError("tool audit record must be an object")
        expected = {"sequence", "project_id", "role_id", "tool_id", "result"}
        if set(raw) != expected:
            raise ToolAuditError("tool audit record fields do not match X10.6 contract")
        sequence = raw["sequence"]
        if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence <= 0:
            raise ToolAuditError("audit sequence must be a positive integer")
        for field in ("project_id", "role_id", "tool_id"):
            value = raw[field]
            if not isinstance(value, str) or not value or value != value.strip() or any(ch.isspace() for ch in value):
                raise ToolAuditError(f"{field} must be a clean non-empty token")
        try:
            result = ToolResult.from_dict(raw["result"])
        except (ToolResultError, TypeError) as exc:
            raise ToolAuditError("audit result violates X10.5 ToolResult contract") from exc
        if result.role_id != raw["role_id"] or result.tool_id != raw["tool_id"]:
            raise ToolAuditError("audit role/tool binding does not match ToolResult")
        return cls(sequence, raw["project_id"], raw["role_id"], raw["tool_id"], result)

    def to_dict(self) -> dict[str, Any]:
        return {
            "sequence": self.sequence,
            "project_id": self.project_id,
            "role_id": self.role_id,
            "tool_id": self.tool_id,
            "result": self.result.to_dict(),
        }


def _audit_path(handle: ProjectHandle) -> Path:
    if not isinstance(handle, ProjectHandle):
        raise TypeError("handle must be ProjectHandle")
    handle.layout.validate_directories()
    parent = handle.layout.data_directories["ai_runs"]
    if parent.is_symlink() or not parent.is_dir():
        raise ToolAuditError("ai_runs audit directory is invalid")
    path = parent / TOOL_AUDIT_FILENAME
    if path.is_symlink():
        raise ToolAuditError("tool audit log must not be a symbolic link")
    if path.exists() and not path.is_file():
        raise ToolAuditError("tool audit log path must be a regular file")
    return path


def _decode_line(raw: bytes, *, expected_project_id: str) -> ToolAuditRecord[Any]:
    if len(raw) > MAX_TOOL_AUDIT_RECORD_BYTES:
        raise ToolAuditError("tool audit record exceeds maximum size")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ToolAuditError("tool audit record is not valid UTF-8 JSON") from exc
    record = ToolAuditRecord.from_dict(payload)
    if record.project_id != expected_project_id:
        raise ToolAuditError("tool audit project binding mismatch")
    return record


def read_tool_audit(handle: ProjectHandle) -> tuple[ToolAuditRecord[Any], ...]:
    path = _audit_path(handle)
    if not path.exists():
        return ()
    records: list[ToolAuditRecord[Any]] = []
    with path.open("rb") as stream:
        for index, line in enumerate(stream, start=1):
            if not line.endswith(b"\n"):
                raise ToolAuditError("tool audit log contains a partial record")
            record = _decode_line(line[:-1], expected_project_id=handle.project_id.value)
            if record.sequence != index:
                raise ToolAuditError("tool audit sequence is not contiguous")
            records.append(record)
    return tuple(records)


def append_tool_audit(*, handle: ProjectHandle, result: ToolResult[T]) -> ToolAuditRecord[T]:
    if not isinstance(result, ToolResult):
        raise TypeError("result must be ToolResult")
    path = _audit_path(handle)
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        with os.fdopen(os.dup(fd), "rb", closefd=True) as reader:
            lines = reader.readlines()
        previous: list[ToolAuditRecord[Any]] = []
        for index, line in enumerate(lines, start=1):
            if not line.endswith(b"\n"):
                raise ToolAuditError("tool audit log contains a partial record")
            record = _decode_line(line[:-1], expected_project_id=handle.project_id.value)
            if record.sequence != index:
                raise ToolAuditError("tool audit sequence is not contiguous")
            previous.append(record)
        record = ToolAuditRecord(
            sequence=len(previous) + 1,
            project_id=handle.project_id.value,
            role_id=result.role_id,
            tool_id=result.tool_id,
            result=result,
        )
        encoded = (json.dumps(record.to_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
        if len(encoded) > MAX_TOOL_AUDIT_RECORD_BYTES:
            raise ToolAuditError("tool audit record exceeds maximum size")
        os.write(fd, encoded)
        os.fsync(fd)
        return record
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


def run_tool_audited(
    *,
    handle: ProjectHandle,
    role_id: str,
    tool_id: str,
    operation: Callable[[], T],
    timeout_s: float,
) -> ToolResult[T]:
    """Run through X10.5 and persist exactly one project-local audit result."""
    result = run_tool_structured(
        role_id=role_id,
        tool_id=tool_id,
        operation=operation,
        timeout_s=timeout_s,
    )
    append_tool_audit(handle=handle, result=result)
    return result
