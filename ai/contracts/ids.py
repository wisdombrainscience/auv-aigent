from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import re
from types import MappingProxyType
from typing import Any, ClassVar, Iterable, Mapping


ID_CONTRACT_VERSION = "1.0"


class CanonicalIdError(ValueError):
    """Raised when a V8 control-plane identifier violates the ID contract."""


class DuplicateCanonicalIdError(CanonicalIdError):
    """Raised when a collection contains the same canonical ID more than once."""


class IdKind(str, Enum):
    """Entity kinds introduced by the V8 engineering control plane."""

    PROJECT = "project"
    REQUIREMENT = "requirement"
    TASK = "task"
    ARTIFACT = "artifact"
    AGENT_RUN = "agent_run"
    REVIEW = "review"
    VALIDATION = "validation"
    ISSUE = "issue"


@dataclass(frozen=True)
class _IdSpec:
    prefix: str
    width: int

    @property
    def pattern(self) -> re.Pattern[str]:
        return re.compile(rf"^{re.escape(self.prefix)}-[0-9]{{{self.width}}}$")

    @property
    def maximum_sequence(self) -> int:
        return (10**self.width) - 1


_ID_SPECS: Mapping[IdKind, _IdSpec] = MappingProxyType(
    {
        # Project/Issue formats are anchored to examples in the frozen V8 baseline:
        # UAV-PROJ-000001 and ISSUE-0041.
        IdKind.PROJECT: _IdSpec("UAV-PROJ", 6),
        IdKind.REQUIREMENT: _IdSpec("REQ", 6),
        IdKind.TASK: _IdSpec("TASK", 6),
        IdKind.ARTIFACT: _IdSpec("ART", 6),
        IdKind.AGENT_RUN: _IdSpec("AI-RUN", 6),
        IdKind.REVIEW: _IdSpec("REVIEW", 6),
        IdKind.VALIDATION: _IdSpec("VALID", 6),
        IdKind.ISSUE: _IdSpec("ISSUE", 4),
    }
)

_PREFIX_ORDER: tuple[tuple[str, IdKind], ...] = tuple(
    sorted(
        ((spec.prefix, kind) for kind, spec in _ID_SPECS.items()),
        key=lambda item: len(item[0]),
        reverse=True,
    )
)


@dataclass(frozen=True, order=True)
class CanonicalId:
    """Validated, immutable V8 engineering control-plane identifier.

    This contract intentionally does not allocate sequence numbers. Persistent
    allocation belongs to the later Project State phase. C1.1 only defines
    formatting, parsing, serialization, and duplicate rejection.

    Existing UAV runtime identifiers such as NAV-001 and MISSION-001 are not
    changed or reinterpreted by this type.
    """

    kind: IdKind
    value: str

    _SERIALIZED_KEYS: ClassVar[frozenset[str]] = frozenset({"kind", "value"})

    def __post_init__(self) -> None:
        kind = self.kind
        if not isinstance(kind, IdKind):
            try:
                kind = IdKind(kind)
            except (TypeError, ValueError) as exc:
                raise CanonicalIdError(f"unknown ID kind: {self.kind!r}") from exc
            object.__setattr__(self, "kind", kind)

        if not isinstance(self.value, str):
            raise CanonicalIdError("canonical ID value must be a string")
        if not self.value:
            raise CanonicalIdError("canonical ID value must not be empty")
        if self.value != self.value.strip():
            raise CanonicalIdError("canonical ID must not contain surrounding whitespace")
        if any(ch.isspace() for ch in self.value):
            raise CanonicalIdError("canonical ID must not contain whitespace")

        spec = _ID_SPECS[kind]
        if spec.pattern.fullmatch(self.value) is None:
            raise CanonicalIdError(
                f"invalid {kind.value} ID {self.value!r}; expected "
                f"{spec.prefix}- followed by exactly {spec.width} digits"
            )

        sequence = int(self.value.rsplit("-", 1)[1])
        if sequence <= 0:
            raise CanonicalIdError("canonical ID sequence must be greater than zero")

    @property
    def sequence(self) -> int:
        return int(self.value.rsplit("-", 1)[1])

    @classmethod
    def from_sequence(cls, kind: IdKind | str, sequence: int) -> CanonicalId:
        try:
            canonical_kind = kind if isinstance(kind, IdKind) else IdKind(kind)
        except (TypeError, ValueError) as exc:
            raise CanonicalIdError(f"unknown ID kind: {kind!r}") from exc

        if isinstance(sequence, bool) or not isinstance(sequence, int):
            raise CanonicalIdError("canonical ID sequence must be an integer")

        spec = _ID_SPECS[canonical_kind]
        if sequence <= 0 or sequence > spec.maximum_sequence:
            raise CanonicalIdError(
                f"{canonical_kind.value} sequence must be in "
                f"1..{spec.maximum_sequence}"
            )
        return cls(canonical_kind, f"{spec.prefix}-{sequence:0{spec.width}d}")

    @classmethod
    def parse(cls, raw: str) -> CanonicalId:
        if not isinstance(raw, str):
            raise CanonicalIdError("canonical ID must be parsed from a string")
        if not raw:
            raise CanonicalIdError("canonical ID value must not be empty")
        if raw != raw.strip() or any(ch.isspace() for ch in raw):
            raise CanonicalIdError("canonical ID must not contain whitespace")

        for prefix, kind in _PREFIX_ORDER:
            if raw.startswith(prefix + "-"):
                return cls(kind, raw)
        raise CanonicalIdError(f"unknown canonical ID prefix: {raw!r}")

    def to_dict(self) -> dict[str, str]:
        return {"kind": self.kind.value, "value": self.value}

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> CanonicalId:
        if not isinstance(raw, Mapping):
            raise CanonicalIdError("canonical ID payload must be a mapping")
        keys = frozenset(raw.keys())
        if keys != cls._SERIALIZED_KEYS:
            missing = sorted(cls._SERIALIZED_KEYS - keys)
            extra = sorted(keys - cls._SERIALIZED_KEYS)
            detail: list[str] = []
            if missing:
                detail.append("missing=" + ",".join(missing))
            if extra:
                detail.append("extra=" + ",".join(str(item) for item in extra))
            raise CanonicalIdError(
                "canonical ID payload must contain exactly kind,value"
                + (" (" + "; ".join(detail) + ")" if detail else "")
            )

        try:
            kind = IdKind(raw["kind"])
        except (TypeError, ValueError) as exc:
            raise CanonicalIdError(f"unknown ID kind: {raw.get('kind')!r}") from exc
        return cls(kind=kind, value=raw["value"])

    def __str__(self) -> str:
        return self.value


def ensure_unique_ids(
    values: Iterable[CanonicalId | str],
) -> tuple[CanonicalId, ...]:
    """Parse IDs and deterministically reject duplicate/colliding values."""

    result: list[CanonicalId] = []
    seen: set[str] = set()

    for item in values:
        canonical = item if isinstance(item, CanonicalId) else CanonicalId.parse(item)
        if canonical.value in seen:
            raise DuplicateCanonicalIdError(
                f"duplicate canonical ID: {canonical.value}"
            )
        seen.add(canonical.value)
        result.append(canonical)

    return tuple(result)
