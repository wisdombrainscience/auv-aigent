from __future__ import annotations

from enum import Enum
from types import MappingProxyType
from typing import Mapping


ARTIFACT_STATE_CONTRACT_VERSION = "1.0"


class ArtifactStateError(ValueError):
    """Base error for the Phase-1 artifact-state contract."""


class InvalidArtifactStateTransition(ArtifactStateError):
    """Raised when an artifact-state transition is not allowed."""


class ArtifactState(str, Enum):
    """Canonical persisted states for one immutable artifact version."""

    DRAFT = "DRAFT"
    REVIEWED = "REVIEWED"
    VALIDATED = "VALIDATED"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    STALE = "STALE"
    SUPERSEDED = "SUPERSEDED"


# A specific artifact version never becomes active again after entering one of
# these historical states. Rework produces a new artifact version later; it
# does not mutate a rejected/stale/superseded version back to DRAFT.
HISTORICAL_ARTIFACT_STATES = frozenset(
    {
        ArtifactState.REJECTED,
        ArtifactState.STALE,
        ArtifactState.SUPERSEDED,
    }
)

# Only APPROVED is eligible for later integration. "Current" is intentionally
# not decided here because currentness depends on lineage/dependencies, which
# belongs to Phase 4 Artifact Registry + Lineage.
INTEGRATION_ELIGIBLE_ARTIFACT_STATES = frozenset({ArtifactState.APPROVED})


_ALLOWED_TRANSITIONS: Mapping[ArtifactState, frozenset[ArtifactState]] = (
    MappingProxyType(
        {
            ArtifactState.DRAFT: frozenset(
                {
                    ArtifactState.REVIEWED,
                    ArtifactState.REJECTED,
                    ArtifactState.STALE,
                    ArtifactState.SUPERSEDED,
                }
            ),
            ArtifactState.REVIEWED: frozenset(
                {
                    ArtifactState.VALIDATED,
                    ArtifactState.REJECTED,
                    ArtifactState.STALE,
                    ArtifactState.SUPERSEDED,
                }
            ),
            ArtifactState.VALIDATED: frozenset(
                {
                    ArtifactState.APPROVED,
                    ArtifactState.REJECTED,
                    ArtifactState.STALE,
                    ArtifactState.SUPERSEDED,
                }
            ),
            # APPROVED is not historical/terminal: a later dependency change
            # can make this exact version STALE, or a newer version can replace
            # it and mark it SUPERSEDED.
            ArtifactState.APPROVED: frozenset(
                {
                    ArtifactState.STALE,
                    ArtifactState.SUPERSEDED,
                }
            ),
            ArtifactState.REJECTED: frozenset(),
            ArtifactState.STALE: frozenset(),
            ArtifactState.SUPERSEDED: frozenset(),
        }
    )
)


def parse_artifact_state(raw: ArtifactState | str) -> ArtifactState:
    """Parse one canonical state without silently normalizing persisted data."""

    if isinstance(raw, ArtifactState):
        return raw
    if not isinstance(raw, str):
        raise ArtifactStateError("artifact state must be a string or ArtifactState")
    if not raw:
        raise ArtifactStateError("artifact state must not be empty")
    if raw != raw.strip() or any(ch.isspace() for ch in raw):
        raise ArtifactStateError("artifact state must not contain whitespace")
    try:
        return ArtifactState(raw)
    except ValueError as exc:
        raise ArtifactStateError(f"unknown artifact state: {raw!r}") from exc


def serialize_artifact_state(state: ArtifactState | str) -> str:
    """Serialize a canonical state to its stable persisted wire value."""

    return parse_artifact_state(state).value


def allowed_artifact_transitions(
    state: ArtifactState | str,
) -> frozenset[ArtifactState]:
    """Return the immutable set of legal direct successor states."""

    canonical = parse_artifact_state(state)
    return _ALLOWED_TRANSITIONS[canonical]


def can_transition_artifact_state(
    current: ArtifactState | str,
    target: ArtifactState | str,
) -> bool:
    current_state = parse_artifact_state(current)
    target_state = parse_artifact_state(target)
    return target_state in _ALLOWED_TRANSITIONS[current_state]


def require_artifact_transition(
    current: ArtifactState | str,
    target: ArtifactState | str,
) -> ArtifactState:
    """Validate one direct transition and return the canonical target state."""

    current_state = parse_artifact_state(current)
    target_state = parse_artifact_state(target)
    if target_state not in _ALLOWED_TRANSITIONS[current_state]:
        raise InvalidArtifactStateTransition(
            "invalid artifact transition: "
            f"{current_state.value} -> {target_state.value}"
        )
    return target_state


def is_historical_artifact_state(state: ArtifactState | str) -> bool:
    """True when this artifact version must never re-enter the active lifecycle."""

    return parse_artifact_state(state) in HISTORICAL_ARTIFACT_STATES


def is_integration_eligible_artifact_state(state: ArtifactState | str) -> bool:
    """State-only eligibility; lineage/currentness is deliberately out of scope."""

    return (
        parse_artifact_state(state)
        in INTEGRATION_ELIGIBLE_ARTIFACT_STATES
    )
