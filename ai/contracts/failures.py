from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import math
import re
from types import MappingProxyType
from typing import Any, Mapping, TypeAlias


FAILURE_TAXONOMY_CONTRACT_VERSION = "1.0"


class FailureTaxonomyError(ValueError):
    """Raised when a structured failure violates the C1.8 contract."""


class FailureClass(str, Enum):
    """Canonical failure classes frozen for the V8 engineering control plane."""

    INFRA = "INFRA"
    PROVIDER = "PROVIDER"
    MODEL = "MODEL"
    TOOL = "TOOL"
    PLAN = "PLAN"
    TASK = "TASK"
    REVIEW = "REVIEW"
    VALIDATION = "VALIDATION"
    SIMULATION = "SIMULATION"
    VERIFICATION = "VERIFICATION"
    USER_INPUT = "USER_INPUT"


FAILURE_CLASS_DESCRIPTIONS: Mapping[FailureClass, str] = MappingProxyType(
    {
        FailureClass.INFRA: (
            "Local runtime, process, filesystem, resource, dependency, or host "
            "infrastructure failure."
        ),
        FailureClass.PROVIDER: (
            "Local AI provider transport, availability, timeout, or provider "
            "protocol failure."
        ),
        FailureClass.MODEL: (
            "Model response/content failure such as invalid structured output."
        ),
        FailureClass.TOOL: (
            "Bounded tool execution, sandbox, filesystem-tool, or tool-contract "
            "failure."
        ),
        FailureClass.PLAN: (
            "Task-plan proposal or deterministic plan-validation failure."
        ),
        FailureClass.TASK: (
            "Task lifecycle/execution failure not more specifically classified."
        ),
        FailureClass.REVIEW: (
            "Advisory review-stage failure or exhausted bounded review/rework path."
        ),
        FailureClass.VALIDATION: (
            "Deterministic engineering validation failure."
        ),
        FailureClass.SIMULATION: (
            "Simulation execution/environment failure, distinct from verifier "
            "verdict failure."
        ),
        FailureClass.VERIFICATION: (
            "Independent deterministic verification failure or invalid verifier "
            "evidence/result."
        ),
        FailureClass.USER_INPUT: (
            "Required user information is missing, invalid, or requires explicit "
            "human clarification/decision."
        ),
    }
)


# Failure codes remain extensible but must be stable, machine-readable, and
# category-prefixed. Example: MODEL_INVALID_OUTPUT.
_CODE_RE = re.compile(r"^[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+$")

JSONScalar: TypeAlias = None | bool | int | float | str
JSONValue: TypeAlias = (
    JSONScalar
    | tuple["JSONValue", ...]
    | Mapping[str, "JSONValue"]
)

_FAILURE_KEYS = frozenset({"class", "code", "message", "details"})


def parse_failure_class(raw: FailureClass | str) -> FailureClass:
    if isinstance(raw, FailureClass):
        return raw
    if not isinstance(raw, str):
        raise FailureTaxonomyError(
            "failure class must be a string or FailureClass"
        )
    if not raw:
        raise FailureTaxonomyError("failure class must not be empty")
    if raw != raw.strip() or any(ch.isspace() for ch in raw):
        raise FailureTaxonomyError(
            "failure class must not contain whitespace"
        )
    try:
        return FailureClass(raw)
    except ValueError as exc:
        raise FailureTaxonomyError(
            f"unknown failure class: {raw!r}"
        ) from exc


def serialize_failure_class(value: FailureClass | str) -> str:
    return parse_failure_class(value).value


def describe_failure_class(value: FailureClass | str) -> str:
    return FAILURE_CLASS_DESCRIPTIONS[parse_failure_class(value)]


def _require_clean_text(value: Any, *, field: str) -> str:
    if not isinstance(value, str):
        raise FailureTaxonomyError(f"{field} must be a string")
    if not value:
        raise FailureTaxonomyError(f"{field} must not be empty")
    if value != value.strip():
        raise FailureTaxonomyError(
            f"{field} must not contain surrounding whitespace"
        )
    return value


def _validate_failure_code(
    raw: Any,
    *,
    failure_class: FailureClass,
) -> str:
    code = _require_clean_text(raw, field="code")
    if not _CODE_RE.fullmatch(code):
        raise FailureTaxonomyError(
            "failure code must be uppercase machine-readable tokens joined by '_'"
        )
    prefix = failure_class.value + "_"
    if not code.startswith(prefix):
        raise FailureTaxonomyError(
            f"failure code {code!r} must start with {prefix!r}"
        )
    return code


def _freeze_json(value: Any, *, path: str) -> JSONValue:
    if value is None or isinstance(value, (bool, str)):
        return value

    if isinstance(value, int):
        return value

    if isinstance(value, float):
        if not math.isfinite(value):
            raise FailureTaxonomyError(
                f"{path} contains a non-finite number"
            )
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
                raise FailureTaxonomyError(
                    f"{path} contains a non-string object key"
                )
            if not key:
                raise FailureTaxonomyError(
                    f"{path} contains an empty object key"
                )
            frozen[key] = _freeze_json(
                item,
                path=f"{path}.{key}",
            )
        return MappingProxyType(frozen)

    raise FailureTaxonomyError(
        f"{path} contains unsupported JSON value type "
        f"{type(value).__name__}"
    )


def _thaw_json(value: JSONValue) -> Any:
    if isinstance(value, tuple):
        return [_thaw_json(item) for item in value]
    if isinstance(value, Mapping):
        return {key: _thaw_json(item) for key, item in value.items()}
    return value


@dataclass(frozen=True)
class FailureDescriptor:
    """Immutable structured failure classification.

    C1.8 intentionally does not carry retry counts, retry permission, task-state
    transitions, or automatic recovery actions. Those policies belong to the
    later bounded Task Engine / Orchestrator. A failure describes what happened;
    it does not authorize another attempt.
    """

    failure_class: FailureClass
    code: str
    message: str
    details: Mapping[str, JSONValue]

    @classmethod
    def from_dict(
        cls,
        raw: Mapping[str, Any],
    ) -> "FailureDescriptor":
        if not isinstance(raw, Mapping):
            raise FailureTaxonomyError(
                "failure payload must be an object"
            )

        keys = frozenset(raw.keys())
        if keys != _FAILURE_KEYS:
            missing = sorted(_FAILURE_KEYS - keys)
            extra = sorted(keys - _FAILURE_KEYS)
            parts: list[str] = []
            if missing:
                parts.append("missing=" + ",".join(missing))
            if extra:
                parts.append(
                    "extra=" + ",".join(str(item) for item in extra)
                )
            raise FailureTaxonomyError(
                "failure payload must contain exactly the C1.8 fields"
                + (" (" + "; ".join(parts) + ")" if parts else "")
            )

        failure_class = parse_failure_class(raw["class"])
        code = _validate_failure_code(
            raw["code"],
            failure_class=failure_class,
        )
        message = _require_clean_text(
            raw["message"],
            field="message",
        )

        raw_details = raw["details"]
        if not isinstance(raw_details, Mapping):
            raise FailureTaxonomyError(
                "details must be an object"
            )
        details = _freeze_json(
            raw_details,
            path="details",
        )
        assert isinstance(details, Mapping)

        return cls(
            failure_class=failure_class,
            code=code,
            message=message,
            details=details,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "class": self.failure_class.value,
            "code": self.code,
            "message": self.message,
            "details": _thaw_json(self.details),
        }


def parse_failure_descriptor(
    raw: Mapping[str, Any],
) -> FailureDescriptor:
    return FailureDescriptor.from_dict(raw)
