from __future__ import annotations

from enum import Enum
from types import MappingProxyType
from typing import Mapping


PROVENANCE_CONTRACT_VERSION = "1.0"


class ProvenanceError(ValueError):
    """Raised when a provenance classification is malformed or unknown."""


class ProvenanceClass(str, Enum):
    """Canonical provenance classes for engineering claims and values."""

    USER_FACT = "USER_FACT"
    VERIFIED_FACT = "VERIFIED_FACT"
    USER_REQUIREMENT = "USER_REQUIREMENT"
    ENGINEERING_ASSUMPTION = "ENGINEERING_ASSUMPTION"
    AI_PROPOSAL = "AI_PROPOSAL"
    CALCULATED_VALUE = "CALCULATED_VALUE"
    SIMULATION_RESULT = "SIMULATION_RESULT"
    VERIFIER_RESULT = "VERIFIER_RESULT"


# Human-readable contract descriptions only. These strings do not grant authority,
# mutate project state, or decide whether a claim is accepted.
PROVENANCE_DESCRIPTIONS: Mapping[ProvenanceClass, str] = MappingProxyType(
    {
        ProvenanceClass.USER_FACT: (
            "A factual assertion supplied by the user; not independently verified "
            "merely because it was supplied."
        ),
        ProvenanceClass.VERIFIED_FACT: (
            "A factual claim that has passed an explicit verification step outside "
            "the claim itself."
        ),
        ProvenanceClass.USER_REQUIREMENT: (
            "A user-stated requirement or constraint for the engineering project."
        ),
        ProvenanceClass.ENGINEERING_ASSUMPTION: (
            "An explicit engineering assumption that must remain distinguishable "
            "from facts and requirements."
        ),
        ProvenanceClass.AI_PROPOSAL: (
            "A model-generated proposal; advisory until later review/validation "
            "and approval gates accept a resulting artifact."
        ),
        ProvenanceClass.CALCULATED_VALUE: (
            "A value produced by a documented calculation; calculation provenance "
            "does not by itself imply simulation or verifier authority."
        ),
        ProvenanceClass.SIMULATION_RESULT: (
            "A result observed or produced by the simulation execution plane."
        ),
        ProvenanceClass.VERIFIER_RESULT: (
            "A result issued by the independent deterministic verifier."
        ),
    }
)


def parse_provenance_class(
    raw: ProvenanceClass | str,
) -> ProvenanceClass:
    """Parse one canonical provenance class without normalization."""

    if isinstance(raw, ProvenanceClass):
        return raw
    if not isinstance(raw, str):
        raise ProvenanceError(
            "provenance class must be a string or ProvenanceClass"
        )
    if not raw:
        raise ProvenanceError("provenance class must not be empty")
    if raw != raw.strip() or any(ch.isspace() for ch in raw):
        raise ProvenanceError("provenance class must not contain whitespace")
    try:
        return ProvenanceClass(raw)
    except ValueError as exc:
        raise ProvenanceError(f"unknown provenance class: {raw!r}") from exc


def serialize_provenance_class(
    value: ProvenanceClass | str,
) -> str:
    """Serialize to the stable persisted wire value."""

    return parse_provenance_class(value).value


def describe_provenance_class(
    value: ProvenanceClass | str,
) -> str:
    """Return the frozen semantic description for one class."""

    canonical = parse_provenance_class(value)
    return PROVENANCE_DESCRIPTIONS[canonical]
