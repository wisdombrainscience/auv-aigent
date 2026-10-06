from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping

from ai.contracts.ids import (
    CanonicalId,
    CanonicalIdError,
    IdKind,
)
from ai.contracts.provenance import (
    ProvenanceClass,
    ProvenanceError,
    parse_provenance_class,
)


REQUIREMENT_SET_CONTRACT_VERSION = "1.0"

# Bounded contract limits. These prevent an unbounded model/user payload from
# becoming a Project State object. S3.4 does not truncate or silently rewrite.
MAX_REQUIREMENTS_PER_SET = 256
MAX_REQUIREMENT_TEXT_BYTES = 16 * 1024


class RequirementContractError(ValueError):
    """Raised when a requirement or RequirementSet violates S3.4."""


class RequirementSetStatus(str, Enum):
    """RequirementSet lifecycle states frozen by the V8 baseline."""

    DRAFT = "DRAFT"
    APPROVED = "APPROVED"


_REQUIREMENT_KEYS = frozenset(
    {
        "requirement_id",
        "text",
        "provenance",
    }
)

_REQUIREMENT_SET_KEYS = frozenset(
    {
        "project_id",
        "status",
        "requirements",
    }
)


def _parse_canonical_id(
    raw: CanonicalId | str,
    *,
    kind: IdKind,
    field: str,
) -> CanonicalId:
    try:
        value = raw if isinstance(raw, CanonicalId) else CanonicalId.parse(raw)
    except (CanonicalIdError, TypeError) as exc:
        raise RequirementContractError(
            f"{field} must be a valid canonical {kind.value} ID"
        ) from exc

    if value.kind is not kind:
        raise RequirementContractError(
            f"{field} must be a canonical {kind.value} ID"
        )
    return value


def _parse_status(raw: RequirementSetStatus | str) -> RequirementSetStatus:
    if isinstance(raw, RequirementSetStatus):
        return raw
    if not isinstance(raw, str):
        raise RequirementContractError(
            "status must be a string or RequirementSetStatus"
        )
    if not raw or raw != raw.strip() or any(ch.isspace() for ch in raw):
        raise RequirementContractError(
            "status must be one exact RequirementSetStatus value"
        )
    try:
        return RequirementSetStatus(raw)
    except ValueError as exc:
        raise RequirementContractError(
            "status must be DRAFT or APPROVED"
        ) from exc


def _require_requirement_text(raw: Any) -> str:
    if not isinstance(raw, str):
        raise RequirementContractError("requirement text must be a string")
    if not raw:
        raise RequirementContractError("requirement text must not be empty")
    if raw != raw.strip():
        raise RequirementContractError(
            "requirement text must not contain surrounding whitespace"
        )
    if "\x00" in raw:
        raise RequirementContractError(
            "requirement text must not contain NUL"
        )

    size = len(raw.encode("utf-8"))
    if size > MAX_REQUIREMENT_TEXT_BYTES:
        raise RequirementContractError(
            "requirement text exceeds MAX_REQUIREMENT_TEXT_BYTES"
        )
    return raw


@dataclass(frozen=True)
class Requirement:
    """One immutable user requirement.

    S3.4 preserves the user's requirement statement and canonical provenance.
    It does not infer facts, assumptions, priority, verification status, or
    engineering feasibility.
    """

    requirement_id: CanonicalId
    text: str
    provenance: ProvenanceClass = ProvenanceClass.USER_REQUIREMENT

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "requirement_id",
            _parse_canonical_id(
                self.requirement_id,
                kind=IdKind.REQUIREMENT,
                field="requirement_id",
            ),
        )
        object.__setattr__(
            self,
            "text",
            _require_requirement_text(self.text),
        )

        try:
            provenance = parse_provenance_class(self.provenance)
        except (ProvenanceError, TypeError) as exc:
            raise RequirementContractError(
                "requirement provenance is invalid"
            ) from exc
        if provenance is not ProvenanceClass.USER_REQUIREMENT:
            raise RequirementContractError(
                "Requirement provenance must be exactly USER_REQUIREMENT"
            )
        object.__setattr__(self, "provenance", provenance)

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "Requirement":
        if not isinstance(raw, Mapping):
            raise RequirementContractError(
                "requirement payload must be an object"
            )
        keys = frozenset(raw.keys())
        if keys != _REQUIREMENT_KEYS:
            missing = sorted(_REQUIREMENT_KEYS - keys)
            extra = sorted(keys - _REQUIREMENT_KEYS)
            detail: list[str] = []
            if missing:
                detail.append("missing=" + ",".join(missing))
            if extra:
                detail.append("extra=" + ",".join(str(item) for item in extra))
            raise RequirementContractError(
                "requirement payload must contain exactly "
                "requirement_id,text,provenance"
                + (" (" + "; ".join(detail) + ")" if detail else "")
            )

        return cls(
            requirement_id=_parse_canonical_id(
                raw["requirement_id"],
                kind=IdKind.REQUIREMENT,
                field="requirement_id",
            ),
            text=_require_requirement_text(raw["text"]),
            provenance=raw["provenance"],
        )

    def to_dict(self) -> dict[str, str]:
        return {
            "requirement_id": self.requirement_id.value,
            "text": self.text,
            "provenance": self.provenance.value,
        }


@dataclass(frozen=True)
class RequirementSet:
    """Immutable S3.4 requirement collection for one project.

    This contract intentionally has no version field. S3.5 owns version
    identity/history/persistence and must wrap this exact content contract
    rather than silently mutating an older RequirementSet.

    S3.4 also does not allocate REQ IDs or grant approval authority.
    """

    project_id: CanonicalId
    status: RequirementSetStatus
    requirements: tuple[Requirement, ...]

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "project_id",
            _parse_canonical_id(
                self.project_id,
                kind=IdKind.PROJECT,
                field="project_id",
            ),
        )
        object.__setattr__(
            self,
            "status",
            _parse_status(self.status),
        )

        if not isinstance(self.requirements, tuple):
            try:
                requirements = tuple(self.requirements)
            except TypeError as exc:
                raise RequirementContractError(
                    "requirements must be an iterable of Requirement"
                ) from exc
            object.__setattr__(self, "requirements", requirements)

        if len(self.requirements) > MAX_REQUIREMENTS_PER_SET:
            raise RequirementContractError(
                "requirements exceeds MAX_REQUIREMENTS_PER_SET"
            )

        seen: set[str] = set()
        normalized: list[Requirement] = []
        for index, item in enumerate(self.requirements):
            if not isinstance(item, Requirement):
                raise RequirementContractError(
                    f"requirements[{index}] must be Requirement"
                )
            key = item.requirement_id.value
            if key in seen:
                raise RequirementContractError(
                    f"duplicate requirement ID: {key}"
                )
            seen.add(key)
            normalized.append(item)

        object.__setattr__(self, "requirements", tuple(normalized))

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "RequirementSet":
        if not isinstance(raw, Mapping):
            raise RequirementContractError(
                "RequirementSet payload must be an object"
            )

        keys = frozenset(raw.keys())
        if keys != _REQUIREMENT_SET_KEYS:
            missing = sorted(_REQUIREMENT_SET_KEYS - keys)
            extra = sorted(keys - _REQUIREMENT_SET_KEYS)
            detail: list[str] = []
            if missing:
                detail.append("missing=" + ",".join(missing))
            if extra:
                detail.append("extra=" + ",".join(str(item) for item in extra))
            raise RequirementContractError(
                "RequirementSet payload must contain exactly "
                "project_id,status,requirements"
                + (" (" + "; ".join(detail) + ")" if detail else "")
            )

        raw_requirements = raw["requirements"]
        if not isinstance(raw_requirements, (list, tuple)):
            raise RequirementContractError(
                "requirements must be an array"
            )
        if len(raw_requirements) > MAX_REQUIREMENTS_PER_SET:
            raise RequirementContractError(
                "requirements exceeds MAX_REQUIREMENTS_PER_SET"
            )

        return cls(
            project_id=_parse_canonical_id(
                raw["project_id"],
                kind=IdKind.PROJECT,
                field="project_id",
            ),
            status=_parse_status(raw["status"]),
            requirements=tuple(
                Requirement.from_dict(item)
                for item in raw_requirements
            ),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "project_id": self.project_id.value,
            "status": self.status.value,
            "requirements": [
                item.to_dict()
                for item in self.requirements
            ],
        }


def parse_requirement_set(raw: Mapping[str, Any]) -> RequirementSet:
    return RequirementSet.from_dict(raw)
