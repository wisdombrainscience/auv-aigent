from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Mapping

from ai.artifacts.schema import ArtifactInputVersion, ArtifactSchemaError
from ai.contracts.ids import CanonicalId, CanonicalIdError, IdKind
from ai.contracts.task_states import TaskState, TaskStateError, parse_task_state


TASK_RECORD_CONTRACT_VERSION = "1.0"
MAX_TASK_TITLE_BYTES = 512
MAX_TASK_ROLE_CHARS = 64
MAX_TASK_PRIORITY = 1_000_000
MAX_TASK_DEPENDENCIES = 30
MAX_TASK_INPUT_ARTIFACTS = 64
MAX_TASK_OUTPUT_SCHEMA_CHARS = 128
MAX_TASK_VALIDATORS = 32
MAX_TASK_VALIDATOR_CHARS = 128
MAX_TASK_ATTEMPTS = 3

_ROLE_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,127}$")

_TASK_RECORD_KEYS = frozenset({
    "task_id",
    "project_id",
    "title",
    "role",
    "priority",
    "dependencies",
    "input_artifacts",
    "output_schema",
    "validators",
    "review_required",
    "attempt",
    "max_attempts",
    "status",
})


class TaskRecordError(ValueError):
    """Raised when a T5.1 task record violates the bounded task contract."""


def _parse_id(raw: CanonicalId | str, *, kind: IdKind, field: str) -> CanonicalId:
    try:
        value = raw if isinstance(raw, CanonicalId) else CanonicalId.parse(raw)
    except (CanonicalIdError, TypeError) as exc:
        raise TaskRecordError(f"{field} must be a canonical {kind.value} ID") from exc
    if value.kind is not kind:
        raise TaskRecordError(f"{field} must be a canonical {kind.value} ID")
    return value


def _clean_title(raw: Any) -> str:
    if not isinstance(raw, str):
        raise TaskRecordError("title must be a string")
    if not raw or raw != raw.strip() or "\x00" in raw:
        raise TaskRecordError("title must be clean non-empty text")
    if len(raw.encode("utf-8")) > MAX_TASK_TITLE_BYTES:
        raise TaskRecordError("title exceeds MAX_TASK_TITLE_BYTES")
    return raw


def _role(raw: Any) -> str:
    if not isinstance(raw, str) or _ROLE_RE.fullmatch(raw) is None:
        raise TaskRecordError("role must match [a-z][a-z0-9_]{0,63}")
    return raw


def _name(raw: Any, *, field: str) -> str:
    if not isinstance(raw, str) or _NAME_RE.fullmatch(raw) is None:
        raise TaskRecordError(
            f"{field} must match [A-Za-z][A-Za-z0-9_.-]{{0,127}}"
        )
    return raw


def _priority(raw: Any) -> int:
    if isinstance(raw, bool) or not isinstance(raw, int):
        raise TaskRecordError("priority must be an integer")
    if not 0 <= raw <= MAX_TASK_PRIORITY:
        raise TaskRecordError(f"priority must be in 0..{MAX_TASK_PRIORITY}")
    return raw


def _attempt(raw: Any, *, field: str, maximum: int) -> int:
    if isinstance(raw, bool) or not isinstance(raw, int):
        raise TaskRecordError(f"{field} must be an integer")
    if not 0 <= raw <= maximum:
        raise TaskRecordError(f"{field} must be in 0..{maximum}")
    return raw


@dataclass(frozen=True)
class TaskRecord:
    """Immutable persisted task snapshot for T5.1.

    T5.1 owns persistence only. It does not execute state transitions, resolve
    dependencies, increment attempts, schedule priority, enforce timeouts, or
    recover RUNNING tasks. Those remain gated to T5.2-T5.11.
    """

    task_id: CanonicalId
    project_id: CanonicalId
    title: str
    role: str
    priority: int
    dependencies: tuple[CanonicalId, ...]
    input_artifacts: tuple[ArtifactInputVersion, ...]
    output_schema: str
    validators: tuple[str, ...]
    review_required: bool
    attempt: int
    max_attempts: int
    status: TaskState

    def __post_init__(self) -> None:
        object.__setattr__(self, "task_id", _parse_id(self.task_id, kind=IdKind.TASK, field="task_id"))
        object.__setattr__(self, "project_id", _parse_id(self.project_id, kind=IdKind.PROJECT, field="project_id"))
        object.__setattr__(self, "title", _clean_title(self.title))
        object.__setattr__(self, "role", _role(self.role))
        object.__setattr__(self, "priority", _priority(self.priority))

        deps_raw = self.dependencies
        if not isinstance(deps_raw, tuple):
            try:
                deps_raw = tuple(deps_raw)
            except TypeError as exc:
                raise TaskRecordError("dependencies must be iterable") from exc
        if len(deps_raw) > MAX_TASK_DEPENDENCIES:
            raise TaskRecordError("dependencies exceeds MAX_TASK_DEPENDENCIES")
        deps: list[CanonicalId] = []
        seen_deps: set[str] = set()
        for item in deps_raw:
            dep = _parse_id(item, kind=IdKind.TASK, field="dependency")
            if dep.value in seen_deps:
                raise TaskRecordError(f"duplicate dependency: {dep.value}")
            seen_deps.add(dep.value)
            deps.append(dep)
        object.__setattr__(self, "dependencies", tuple(deps))

        artifacts_raw = self.input_artifacts
        if not isinstance(artifacts_raw, tuple):
            try:
                artifacts_raw = tuple(artifacts_raw)
            except TypeError as exc:
                raise TaskRecordError("input_artifacts must be iterable") from exc
        if len(artifacts_raw) > MAX_TASK_INPUT_ARTIFACTS:
            raise TaskRecordError("input_artifacts exceeds MAX_TASK_INPUT_ARTIFACTS")
        artifacts: list[ArtifactInputVersion] = []
        seen_artifacts: set[tuple[str, int]] = set()
        for item in artifacts_raw:
            if not isinstance(item, ArtifactInputVersion):
                raise TaskRecordError("input_artifacts entries must be ArtifactInputVersion")
            key = (item.entity_id.value, item.version)
            if key in seen_artifacts:
                raise TaskRecordError(
                    f"duplicate input artifact reference: {item.entity_id.value}@v{item.version}"
                )
            seen_artifacts.add(key)
            artifacts.append(item)
        object.__setattr__(self, "input_artifacts", tuple(artifacts))

        object.__setattr__(self, "output_schema", _name(self.output_schema, field="output_schema"))

        validators_raw = self.validators
        if not isinstance(validators_raw, tuple):
            try:
                validators_raw = tuple(validators_raw)
            except TypeError as exc:
                raise TaskRecordError("validators must be iterable") from exc
        if len(validators_raw) > MAX_TASK_VALIDATORS:
            raise TaskRecordError("validators exceeds MAX_TASK_VALIDATORS")
        validators: list[str] = []
        seen_validators: set[str] = set()
        for item in validators_raw:
            name = _name(item, field="validator")
            if name in seen_validators:
                raise TaskRecordError(f"duplicate validator: {name}")
            seen_validators.add(name)
            validators.append(name)
        object.__setattr__(self, "validators", tuple(validators))

        if not isinstance(self.review_required, bool):
            raise TaskRecordError("review_required must be boolean")

        max_attempts = _attempt(
            self.max_attempts,
            field="max_attempts",
            maximum=MAX_TASK_ATTEMPTS,
        )
        if max_attempts < 1:
            raise TaskRecordError("max_attempts must be at least 1")
        object.__setattr__(self, "max_attempts", max_attempts)

        attempt = _attempt(self.attempt, field="attempt", maximum=MAX_TASK_ATTEMPTS)
        if attempt > max_attempts:
            raise TaskRecordError("attempt must not exceed max_attempts")
        object.__setattr__(self, "attempt", attempt)

        try:
            status = parse_task_state(self.status)
        except (TaskStateError, TypeError) as exc:
            raise TaskRecordError("status is not a valid TaskState") from exc
        object.__setattr__(self, "status", status)

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "TaskRecord":
        if not isinstance(raw, Mapping):
            raise TaskRecordError("task record payload must be an object")
        keys = frozenset(raw.keys())
        if keys != _TASK_RECORD_KEYS:
            missing = sorted(_TASK_RECORD_KEYS - keys)
            extra = sorted(keys - _TASK_RECORD_KEYS)
            detail: list[str] = []
            if missing:
                detail.append("missing=" + ",".join(missing))
            if extra:
                detail.append("extra=" + ",".join(str(x) for x in extra))
            raise TaskRecordError(
                "task record keys do not match T5.1 contract"
                + (" (" + "; ".join(detail) + ")" if detail else "")
            )

        raw_artifacts = raw["input_artifacts"]
        if not isinstance(raw_artifacts, (list, tuple)):
            raise TaskRecordError("input_artifacts must be an array")
        try:
            artifacts = tuple(ArtifactInputVersion.from_dict(item) for item in raw_artifacts)
        except ArtifactSchemaError as exc:
            raise TaskRecordError(f"input_artifacts is invalid: {exc}") from exc

        raw_deps = raw["dependencies"]
        raw_validators = raw["validators"]
        if not isinstance(raw_deps, (list, tuple)):
            raise TaskRecordError("dependencies must be an array")
        if not isinstance(raw_validators, (list, tuple)):
            raise TaskRecordError("validators must be an array")

        return cls(
            task_id=raw["task_id"],
            project_id=raw["project_id"],
            title=raw["title"],
            role=raw["role"],
            priority=raw["priority"],
            dependencies=tuple(raw_deps),
            input_artifacts=artifacts,
            output_schema=raw["output_schema"],
            validators=tuple(raw_validators),
            review_required=raw["review_required"],
            attempt=raw["attempt"],
            max_attempts=raw["max_attempts"],
            status=raw["status"],
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "task_id": self.task_id.value,
            "project_id": self.project_id.value,
            "title": self.title,
            "role": self.role,
            "priority": self.priority,
            "dependencies": [item.value for item in self.dependencies],
            "input_artifacts": [item.to_dict() for item in self.input_artifacts],
            "output_schema": self.output_schema,
            "validators": list(self.validators),
            "review_required": self.review_required,
            "attempt": self.attempt,
            "max_attempts": self.max_attempts,
            "status": self.status.value,
        }
