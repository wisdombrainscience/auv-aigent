from __future__ import annotations

from dataclasses import dataclass
import json
import re
from typing import Any, Mapping

from ai.artifacts.schema import ArtifactInputVersion, ArtifactSchemaError
from ai.contracts.ids import CanonicalId, CanonicalIdError, IdKind
from ai.project.requirement_versions import MAX_REQUIREMENT_VERSION
from ai.tasks.schema import (
    MAX_TASK_DEPENDENCIES,
    MAX_TASK_INPUT_ARTIFACTS,
    MAX_TASK_OUTPUT_SCHEMA_CHARS,
    MAX_TASK_PRIORITY,
    MAX_TASK_ROLE_CHARS,
    MAX_TASK_TITLE_BYTES,
    MAX_TASK_VALIDATOR_CHARS,
    MAX_TASK_VALIDATORS,
)


TASK_PLAN_PROPOSAL_CONTRACT_VERSION = "1.0"

# O8.1 is a structural proposal contract, not the frozen PlanValidator policy.
# The later O8.7 gate owns max_tasks_per_plan=30 and retry-policy enforcement.
# These bounds exist only to keep parsing/serialization finite before that gate.
MAX_TASK_PLAN_PROPOSAL_TASKS = 256
MAX_TASK_PLAN_PROPOSAL_BYTES = 512 * 1024
MAX_PROPOSED_MAX_ATTEMPTS = 1_000

_ROLE_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,127}$")

_TASK_KEYS = frozenset(
    {
        "task_id",
        "title",
        "role",
        "priority",
        "dependencies",
        "input_artifacts",
        "output_schema",
        "validators",
        "review_required",
        "max_attempts",
    }
)
_PLAN_KEYS = frozenset({"project_id", "requirement_version", "tasks"})


class TaskPlanProposalError(ValueError):
    """Raised when an O8.1 task-plan proposal violates its wire contract."""


def _parse_id(raw: CanonicalId | str, *, kind: IdKind, field: str) -> CanonicalId:
    try:
        value = raw if isinstance(raw, CanonicalId) else CanonicalId.parse(raw)
    except (CanonicalIdError, TypeError) as exc:
        raise TaskPlanProposalError(
            f"{field} must be a canonical {kind.value} ID"
        ) from exc
    if value.kind is not kind:
        raise TaskPlanProposalError(
            f"{field} must be a canonical {kind.value} ID"
        )
    return value


def _clean_title(raw: Any) -> str:
    if not isinstance(raw, str):
        raise TaskPlanProposalError("title must be a string")
    if not raw or raw != raw.strip() or "\x00" in raw:
        raise TaskPlanProposalError("title must be clean non-empty text")
    if len(raw.encode("utf-8")) > MAX_TASK_TITLE_BYTES:
        raise TaskPlanProposalError("title exceeds MAX_TASK_TITLE_BYTES")
    return raw


def _clean_role(raw: Any) -> str:
    if not isinstance(raw, str) or len(raw) > MAX_TASK_ROLE_CHARS:
        raise TaskPlanProposalError("role exceeds MAX_TASK_ROLE_CHARS")
    if _ROLE_RE.fullmatch(raw) is None:
        raise TaskPlanProposalError("role must match [a-z][a-z0-9_]{0,63}")
    return raw


def _clean_name(raw: Any, *, field: str) -> str:
    limit = (
        MAX_TASK_OUTPUT_SCHEMA_CHARS
        if field == "output_schema"
        else MAX_TASK_VALIDATOR_CHARS
    )
    if not isinstance(raw, str) or len(raw) > limit:
        raise TaskPlanProposalError(f"{field} exceeds its length limit")
    if _NAME_RE.fullmatch(raw) is None:
        raise TaskPlanProposalError(
            f"{field} must match [A-Za-z][A-Za-z0-9_.-]{{0,127}}"
        )
    return raw


def _clean_priority(raw: Any) -> int:
    if isinstance(raw, bool) or not isinstance(raw, int):
        raise TaskPlanProposalError("priority must be an integer")
    if not 0 <= raw <= MAX_TASK_PRIORITY:
        raise TaskPlanProposalError(
            f"priority must be in 0..{MAX_TASK_PRIORITY}"
        )
    return raw


def _clean_max_attempts(raw: Any) -> int:
    if isinstance(raw, bool) or not isinstance(raw, int):
        raise TaskPlanProposalError("max_attempts must be an integer")
    if not 1 <= raw <= MAX_PROPOSED_MAX_ATTEMPTS:
        raise TaskPlanProposalError(
            f"max_attempts must be in 1..{MAX_PROPOSED_MAX_ATTEMPTS}"
        )
    return raw


def _clean_requirement_version(raw: Any) -> int:
    if isinstance(raw, bool) or not isinstance(raw, int):
        raise TaskPlanProposalError("requirement_version must be an integer")
    if not 1 <= raw <= MAX_REQUIREMENT_VERSION:
        raise TaskPlanProposalError(
            "requirement_version is outside the supported range"
        )
    return raw


def _require_exact_keys(raw: Mapping[str, Any], expected: frozenset[str], *, label: str) -> None:
    keys = frozenset(raw.keys())
    if keys == expected:
        return
    missing = sorted(expected - keys)
    extra = sorted(keys - expected)
    detail: list[str] = []
    if missing:
        detail.append("missing=" + ",".join(missing))
    if extra:
        detail.append("extra=" + ",".join(str(item) for item in extra))
    raise TaskPlanProposalError(
        f"{label} keys do not match O8.1 contract"
        + (" (" + "; ".join(detail) + ")" if detail else "")
    )


@dataclass(frozen=True)
class ProposedTask:
    """One non-executable candidate task in a TaskPlanProposal.

    O8.1 deliberately has no project-persistence state: no attempt counter,
    lifecycle status, assigned AgentInstance, run ID, or scheduler state.

    Semantic plan checks are intentionally deferred:
    - duplicate task IDs -> O8.3 PlanValidator
    - known roles -> O8.4
    - known output schemas -> O8.5
    - dependency existence / DAG rules -> O8.6
    - frozen task/retry/resource budgets -> O8.7
    """

    task_id: CanonicalId
    title: str
    role: str
    priority: int
    dependencies: tuple[CanonicalId, ...]
    input_artifacts: tuple[ArtifactInputVersion, ...]
    output_schema: str
    validators: tuple[str, ...]
    review_required: bool
    max_attempts: int

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "task_id",
            _parse_id(self.task_id, kind=IdKind.TASK, field="task_id"),
        )
        object.__setattr__(self, "title", _clean_title(self.title))
        object.__setattr__(self, "role", _clean_role(self.role))
        object.__setattr__(self, "priority", _clean_priority(self.priority))

        deps_raw = self.dependencies
        if not isinstance(deps_raw, tuple):
            try:
                deps_raw = tuple(deps_raw)
            except TypeError as exc:
                raise TaskPlanProposalError("dependencies must be iterable") from exc
        if len(deps_raw) > MAX_TASK_DEPENDENCIES:
            raise TaskPlanProposalError("dependencies exceeds MAX_TASK_DEPENDENCIES")
        dependencies = tuple(
            _parse_id(item, kind=IdKind.TASK, field="dependency")
            for item in deps_raw
        )
        # Duplicate dependency declarations cannot be represented by TaskRecord,
        # so this is wire-shape validation, not DAG/dependency-existence policy.
        dep_values = [item.value for item in dependencies]
        if len(dep_values) != len(set(dep_values)):
            raise TaskPlanProposalError("dependencies contains duplicate TASK IDs")
        object.__setattr__(self, "dependencies", dependencies)

        artifacts_raw = self.input_artifacts
        if not isinstance(artifacts_raw, tuple):
            try:
                artifacts_raw = tuple(artifacts_raw)
            except TypeError as exc:
                raise TaskPlanProposalError("input_artifacts must be iterable") from exc
        if len(artifacts_raw) > MAX_TASK_INPUT_ARTIFACTS:
            raise TaskPlanProposalError(
                "input_artifacts exceeds MAX_TASK_INPUT_ARTIFACTS"
            )
        artifacts: list[ArtifactInputVersion] = []
        seen_artifacts: set[tuple[str, int]] = set()
        for item in artifacts_raw:
            if not isinstance(item, ArtifactInputVersion):
                raise TaskPlanProposalError(
                    "input_artifacts entries must be ArtifactInputVersion"
                )
            key = (item.entity_id.value, item.version)
            if key in seen_artifacts:
                raise TaskPlanProposalError(
                    f"duplicate input artifact reference: {item.entity_id.value}@v{item.version}"
                )
            seen_artifacts.add(key)
            artifacts.append(item)
        object.__setattr__(self, "input_artifacts", tuple(artifacts))

        object.__setattr__(
            self,
            "output_schema",
            _clean_name(self.output_schema, field="output_schema"),
        )

        validators_raw = self.validators
        if not isinstance(validators_raw, tuple):
            try:
                validators_raw = tuple(validators_raw)
            except TypeError as exc:
                raise TaskPlanProposalError("validators must be iterable") from exc
        if len(validators_raw) > MAX_TASK_VALIDATORS:
            raise TaskPlanProposalError("validators exceeds MAX_TASK_VALIDATORS")
        validators: list[str] = []
        seen_validators: set[str] = set()
        for item in validators_raw:
            name = _clean_name(item, field="validator")
            if name in seen_validators:
                raise TaskPlanProposalError(f"duplicate validator: {name}")
            seen_validators.add(name)
            validators.append(name)
        object.__setattr__(self, "validators", tuple(validators))

        if not isinstance(self.review_required, bool):
            raise TaskPlanProposalError("review_required must be boolean")
        object.__setattr__(self, "max_attempts", _clean_max_attempts(self.max_attempts))

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "ProposedTask":
        if not isinstance(raw, Mapping):
            raise TaskPlanProposalError("proposed task payload must be an object")
        _require_exact_keys(raw, _TASK_KEYS, label="proposed task")

        dependencies = raw["dependencies"]
        validators = raw["validators"]
        input_artifacts = raw["input_artifacts"]
        if not isinstance(dependencies, (list, tuple)):
            raise TaskPlanProposalError("dependencies must be an array")
        if not isinstance(validators, (list, tuple)):
            raise TaskPlanProposalError("validators must be an array")
        if not isinstance(input_artifacts, (list, tuple)):
            raise TaskPlanProposalError("input_artifacts must be an array")
        try:
            parsed_artifacts = tuple(
                ArtifactInputVersion.from_dict(item) for item in input_artifacts
            )
        except ArtifactSchemaError as exc:
            raise TaskPlanProposalError(f"input_artifacts is invalid: {exc}") from exc

        return cls(
            task_id=raw["task_id"],
            title=raw["title"],
            role=raw["role"],
            priority=raw["priority"],
            dependencies=tuple(dependencies),
            input_artifacts=parsed_artifacts,
            output_schema=raw["output_schema"],
            validators=tuple(validators),
            review_required=raw["review_required"],
            max_attempts=raw["max_attempts"],
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "task_id": self.task_id.value,
            "title": self.title,
            "role": self.role,
            "priority": self.priority,
            "dependencies": [item.value for item in self.dependencies],
            "input_artifacts": [item.to_dict() for item in self.input_artifacts],
            "output_schema": self.output_schema,
            "validators": list(self.validators),
            "review_required": self.review_required,
            "max_attempts": self.max_attempts,
        }


@dataclass(frozen=True)
class TaskPlanProposal:
    """Immutable non-executable O8.1 plan proposal.

    `requirement_version` binds the proposal to one immutable Project State
    RequirementSet snapshot. O8.1 validates only wire-level structure and does
    not open Project State or determine whether that version is current/approved.
    """

    project_id: CanonicalId
    requirement_version: int
    tasks: tuple[ProposedTask, ...]

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "project_id",
            _parse_id(self.project_id, kind=IdKind.PROJECT, field="project_id"),
        )
        object.__setattr__(
            self,
            "requirement_version",
            _clean_requirement_version(self.requirement_version),
        )

        tasks_raw = self.tasks
        if not isinstance(tasks_raw, tuple):
            try:
                tasks_raw = tuple(tasks_raw)
            except TypeError as exc:
                raise TaskPlanProposalError("tasks must be iterable") from exc
        if not tasks_raw:
            raise TaskPlanProposalError("tasks must contain at least one proposed task")
        if len(tasks_raw) > MAX_TASK_PLAN_PROPOSAL_TASKS:
            raise TaskPlanProposalError(
                "tasks exceeds MAX_TASK_PLAN_PROPOSAL_TASKS"
            )
        if not all(isinstance(item, ProposedTask) for item in tasks_raw):
            raise TaskPlanProposalError("tasks entries must be ProposedTask")
        # Deliberately do not enforce unique task IDs, known roles/schemas,
        # dependency existence, acyclicity, max_tasks_per_plan=30, or retry<=3.
        object.__setattr__(self, "tasks", tuple(tasks_raw))

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "TaskPlanProposal":
        if not isinstance(raw, Mapping):
            raise TaskPlanProposalError("task plan proposal payload must be an object")
        _require_exact_keys(raw, _PLAN_KEYS, label="task plan proposal")
        raw_tasks = raw["tasks"]
        if not isinstance(raw_tasks, (list, tuple)):
            raise TaskPlanProposalError("tasks must be an array")
        return cls(
            project_id=raw["project_id"],
            requirement_version=raw["requirement_version"],
            tasks=tuple(ProposedTask.from_dict(item) for item in raw_tasks),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "project_id": self.project_id.value,
            "requirement_version": self.requirement_version,
            "tasks": [item.to_dict() for item in self.tasks],
        }

    def to_json_bytes(self) -> bytes:
        try:
            payload = json.dumps(
                self.to_dict(),
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
                allow_nan=False,
            ).encode("utf-8")
        except (TypeError, ValueError) as exc:
            raise TaskPlanProposalError(
                "task plan proposal cannot be canonically encoded"
            ) from exc
        if len(payload) > MAX_TASK_PLAN_PROPOSAL_BYTES:
            raise TaskPlanProposalError(
                "serialized proposal exceeds MAX_TASK_PLAN_PROPOSAL_BYTES"
            )
        return payload


def _reject_duplicate_object_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise TaskPlanProposalError(f"duplicate JSON object key: {key}")
        result[key] = value
    return result


def _reject_json_constant(raw: str) -> None:
    raise TaskPlanProposalError(f"non-standard JSON numeric constant is not allowed: {raw}")


def parse_task_plan_proposal(raw: Mapping[str, Any]) -> TaskPlanProposal:
    return TaskPlanProposal.from_dict(raw)


def parse_task_plan_proposal_json(raw: bytes | str) -> TaskPlanProposal:
    if isinstance(raw, bytes):
        if len(raw) > MAX_TASK_PLAN_PROPOSAL_BYTES:
            raise TaskPlanProposalError(
                "serialized proposal exceeds MAX_TASK_PLAN_PROPOSAL_BYTES"
            )
        try:
            text = raw.decode("utf-8", errors="strict")
        except UnicodeDecodeError as exc:
            raise TaskPlanProposalError("proposal JSON must be valid UTF-8") from exc
    elif isinstance(raw, str):
        if len(raw.encode("utf-8")) > MAX_TASK_PLAN_PROPOSAL_BYTES:
            raise TaskPlanProposalError(
                "serialized proposal exceeds MAX_TASK_PLAN_PROPOSAL_BYTES"
            )
        text = raw
    else:
        raise TaskPlanProposalError("proposal JSON must be bytes or string")

    try:
        value = json.loads(
            text,
            object_pairs_hook=_reject_duplicate_object_pairs,
            parse_constant=_reject_json_constant,
        )
    except TaskPlanProposalError:
        raise
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        raise TaskPlanProposalError("proposal JSON is invalid") from exc
    if not isinstance(value, Mapping):
        raise TaskPlanProposalError("proposal JSON root must be an object")
    return TaskPlanProposal.from_dict(value)


def serialize_task_plan_proposal(proposal: TaskPlanProposal) -> bytes:
    if not isinstance(proposal, TaskPlanProposal):
        raise TaskPlanProposalError("proposal must be TaskPlanProposal")
    return proposal.to_json_bytes()
