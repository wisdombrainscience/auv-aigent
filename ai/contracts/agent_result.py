from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import math
from types import MappingProxyType
from typing import Any, Mapping, TypeAlias

from ai.contracts.provenance import (
    ProvenanceError,
    parse_provenance_class,
)


AGENT_RESULT_CONTRACT_VERSION = "1.0"


class AgentResultError(ValueError):
    """Raised when structured agent output violates the C1.5 contract."""


class AgentResultStatus(str, Enum):
    """C1.5 accepts only model proposals, never an approved/final status."""

    PROPOSED = "PROPOSED"


JSONScalar: TypeAlias = None | bool | int | float | str
JSONValue: TypeAlias = (
    JSONScalar
    | tuple["JSONValue", ...]
    | Mapping[str, "JSONValue"]
)


_AGENT_RESULT_KEYS = frozenset(
    {
        "status",
        "claims",
        "assumptions",
        "calculations",
        "artifacts",
        "risks",
        "missing_information",
        "open_questions",
    }
)


def _require_clean_text(value: Any, *, field: str) -> str:
    if not isinstance(value, str):
        raise AgentResultError(f"{field} must be a string")
    if not value:
        raise AgentResultError(f"{field} must not be empty")
    if value != value.strip():
        raise AgentResultError(f"{field} must not contain surrounding whitespace")
    return value


def _freeze_json(value: Any, *, path: str) -> JSONValue:
    """Validate JSON-compatible data and freeze nested containers.

    Booleans are accepted as booleans, but NaN/Infinity are rejected because
    they are not interoperable strict JSON values and must not enter Project
    State through model output.
    """

    if value is None or isinstance(value, (bool, str)):
        return value

    if isinstance(value, int):
        return value

    if isinstance(value, float):
        if not math.isfinite(value):
            raise AgentResultError(f"{path} contains a non-finite number")
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
                raise AgentResultError(f"{path} contains a non-string object key")
            if not key:
                raise AgentResultError(f"{path} contains an empty object key")
            frozen[key] = _freeze_json(item, path=f"{path}.{key}")
        return MappingProxyType(frozen)

    raise AgentResultError(
        f"{path} contains unsupported JSON value type {type(value).__name__}"
    )


def _thaw_json(value: JSONValue) -> Any:
    if isinstance(value, tuple):
        return [_thaw_json(item) for item in value]
    if isinstance(value, Mapping):
        return {key: _thaw_json(item) for key, item in value.items()}
    return value


def _parse_status(raw: Any) -> AgentResultStatus:
    if isinstance(raw, AgentResultStatus):
        return raw
    if not isinstance(raw, str):
        raise AgentResultError("status must be a string or AgentResultStatus")
    if raw != raw.strip() or any(ch.isspace() for ch in raw):
        raise AgentResultError("status must not contain whitespace")
    try:
        status = AgentResultStatus(raw)
    except ValueError as exc:
        raise AgentResultError(
            "agent result status must be exactly PROPOSED"
        ) from exc
    return status


def _parse_sequence(raw: Any, *, field: str) -> tuple[JSONValue, ...]:
    if not isinstance(raw, (list, tuple)):
        raise AgentResultError(f"{field} must be an array")
    return tuple(
        _freeze_json(item, path=f"{field}[{index}]")
        for index, item in enumerate(raw)
    )


def _parse_text_sequence(raw: Any, *, field: str) -> tuple[str, ...]:
    if not isinstance(raw, (list, tuple)):
        raise AgentResultError(f"{field} must be an array")

    result: list[str] = []
    for index, item in enumerate(raw):
        result.append(
            _require_clean_text(item, field=f"{field}[{index}]")
        )
    return tuple(result)


def _validate_claims(
    claims: tuple[JSONValue, ...],
) -> tuple[Mapping[str, JSONValue], ...]:
    result: list[Mapping[str, JSONValue]] = []

    for index, claim in enumerate(claims):
        if not isinstance(claim, Mapping):
            raise AgentResultError(
                f"claims[{index}] must be an object with provenance classification"
            )
        if "classification" not in claim:
            raise AgentResultError(
                f"claims[{index}] must contain classification"
            )

        raw_classification = claim["classification"]
        try:
            canonical = parse_provenance_class(raw_classification)
        except (ProvenanceError, TypeError) as exc:
            raise AgentResultError(
                f"claims[{index}].classification is invalid"
            ) from exc

        # Replace an enum instance or equivalent validated string with the stable
        # wire value. Other claim fields stay schema-open until their later
        # dedicated contracts are frozen.
        normalized = dict(claim)
        normalized["classification"] = canonical.value
        frozen = _freeze_json(normalized, path=f"claims[{index}]")
        assert isinstance(frozen, Mapping)
        result.append(frozen)

    return tuple(result)


@dataclass(frozen=True)
class AgentResult:
    """Minimum structured result emitted by a local engineering agent.

    C1.5 freezes only the top-level envelope from the architecture baseline.
    Nested calculation/artifact/risk schemas are deliberately left for later
    dedicated contracts. Claims must already carry a canonical C1.4 provenance
    classification.

    This object never represents approval. A valid AgentResult is always a
    proposal and remains subject to review/validation/project-state gates.
    """

    status: AgentResultStatus
    claims: tuple[Mapping[str, JSONValue], ...]
    assumptions: tuple[JSONValue, ...]
    calculations: tuple[JSONValue, ...]
    artifacts: tuple[JSONValue, ...]
    risks: tuple[JSONValue, ...]
    missing_information: tuple[str, ...]
    open_questions: tuple[str, ...]

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "AgentResult":
        if not isinstance(raw, Mapping):
            raise AgentResultError("agent result payload must be an object")

        keys = frozenset(raw.keys())
        if keys != _AGENT_RESULT_KEYS:
            missing = sorted(_AGENT_RESULT_KEYS - keys)
            extra = sorted(keys - _AGENT_RESULT_KEYS)
            detail: list[str] = []
            if missing:
                detail.append("missing=" + ",".join(missing))
            if extra:
                detail.append("extra=" + ",".join(str(item) for item in extra))
            raise AgentResultError(
                "agent result payload must contain exactly the C1.5 fields"
                + (" (" + "; ".join(detail) + ")" if detail else "")
            )

        status = _parse_status(raw["status"])
        claims = _validate_claims(
            _parse_sequence(raw["claims"], field="claims")
        )

        return cls(
            status=status,
            claims=claims,
            assumptions=_parse_sequence(
                raw["assumptions"], field="assumptions"
            ),
            calculations=_parse_sequence(
                raw["calculations"], field="calculations"
            ),
            artifacts=_parse_sequence(
                raw["artifacts"], field="artifacts"
            ),
            risks=_parse_sequence(raw["risks"], field="risks"),
            missing_information=_parse_text_sequence(
                raw["missing_information"], field="missing_information"
            ),
            open_questions=_parse_text_sequence(
                raw["open_questions"], field="open_questions"
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "claims": [_thaw_json(item) for item in self.claims],
            "assumptions": [_thaw_json(item) for item in self.assumptions],
            "calculations": [_thaw_json(item) for item in self.calculations],
            "artifacts": [_thaw_json(item) for item in self.artifacts],
            "risks": [_thaw_json(item) for item in self.risks],
            "missing_information": list(self.missing_information),
            "open_questions": list(self.open_questions),
        }


def parse_agent_result(raw: Mapping[str, Any]) -> AgentResult:
    """Convenience parser for model/provider output after JSON decoding."""

    return AgentResult.from_dict(raw)
