from __future__ import annotations

import json

import pytest

from ai.contracts.artifact_states import (
    ARTIFACT_STATE_CONTRACT_VERSION,
    HISTORICAL_ARTIFACT_STATES,
    INTEGRATION_ELIGIBLE_ARTIFACT_STATES,
    ArtifactState,
    ArtifactStateError,
    InvalidArtifactStateTransition,
    allowed_artifact_transitions,
    can_transition_artifact_state,
    is_historical_artifact_state,
    is_integration_eligible_artifact_state,
    parse_artifact_state,
    require_artifact_transition,
    serialize_artifact_state,
)


EXPECTED_STATES = [
    "DRAFT",
    "REVIEWED",
    "VALIDATED",
    "APPROVED",
    "REJECTED",
    "STALE",
    "SUPERSEDED",
]


def test_contract_version_is_explicit() -> None:
    assert ARTIFACT_STATE_CONTRACT_VERSION == "1.0"


def test_frozen_baseline_state_set_and_order_are_exact() -> None:
    assert [state.value for state in ArtifactState] == EXPECTED_STATES


@pytest.mark.parametrize("raw", EXPECTED_STATES)
def test_parse_and_serialize_round_trip(raw: str) -> None:
    state = parse_artifact_state(raw)
    wire = json.loads(json.dumps({"state": serialize_artifact_state(state)}))
    assert wire == {"state": raw}
    assert parse_artifact_state(wire["state"]) is state


@pytest.mark.parametrize(
    "raw",
    [
        "",
        " ",
        "DRAFT ",
        " DRAFT",
        "draft",
        "Reviewed",
        "SUPER SEDED",
        "UNKNOWN",
        1,
        None,
    ],
)
def test_parse_rejects_noncanonical_states(raw: object) -> None:
    with pytest.raises(ArtifactStateError):
        parse_artifact_state(raw)  # type: ignore[arg-type]


def test_nominal_artifact_lifecycle_is_legal() -> None:
    path = [
        ArtifactState.DRAFT,
        ArtifactState.REVIEWED,
        ArtifactState.VALIDATED,
        ArtifactState.APPROVED,
    ]
    for current, target in zip(path, path[1:]):
        assert require_artifact_transition(current, target) is target


@pytest.mark.parametrize(
    "state",
    [
        ArtifactState.DRAFT,
        ArtifactState.REVIEWED,
        ArtifactState.VALIDATED,
    ],
)
def test_preapproval_artifact_can_be_rejected(state: ArtifactState) -> None:
    assert can_transition_artifact_state(state, ArtifactState.REJECTED)


@pytest.mark.parametrize(
    "state",
    [
        ArtifactState.DRAFT,
        ArtifactState.REVIEWED,
        ArtifactState.VALIDATED,
        ArtifactState.APPROVED,
    ],
)
def test_dependency_change_can_make_active_version_stale(
    state: ArtifactState,
) -> None:
    assert can_transition_artifact_state(state, ArtifactState.STALE)


@pytest.mark.parametrize(
    "state",
    [
        ArtifactState.DRAFT,
        ArtifactState.REVIEWED,
        ArtifactState.VALIDATED,
        ArtifactState.APPROVED,
    ],
)
def test_newer_version_can_supersede_active_version(state: ArtifactState) -> None:
    assert can_transition_artifact_state(state, ArtifactState.SUPERSEDED)


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (ArtifactState.DRAFT, ArtifactState.VALIDATED),
        (ArtifactState.DRAFT, ArtifactState.APPROVED),
        (ArtifactState.REVIEWED, ArtifactState.APPROVED),
        (ArtifactState.APPROVED, ArtifactState.REJECTED),
        (ArtifactState.STALE, ArtifactState.DRAFT),
        (ArtifactState.REJECTED, ArtifactState.DRAFT),
        (ArtifactState.SUPERSEDED, ArtifactState.DRAFT),
        (ArtifactState.APPROVED, ArtifactState.APPROVED),
    ],
)
def test_invalid_direct_transitions_are_rejected(
    current: ArtifactState,
    target: ArtifactState,
) -> None:
    assert not can_transition_artifact_state(current, target)
    with pytest.raises(
        InvalidArtifactStateTransition,
        match=rf"{current.value} -> {target.value}",
    ):
        require_artifact_transition(current, target)


def test_historical_states_are_final_for_this_version() -> None:
    assert HISTORICAL_ARTIFACT_STATES == frozenset(
        {
            ArtifactState.REJECTED,
            ArtifactState.STALE,
            ArtifactState.SUPERSEDED,
        }
    )
    for state in HISTORICAL_ARTIFACT_STATES:
        assert is_historical_artifact_state(state)
        assert allowed_artifact_transitions(state) == frozenset()


@pytest.mark.parametrize(
    "state",
    [
        ArtifactState.DRAFT,
        ArtifactState.REVIEWED,
        ArtifactState.VALIDATED,
        ArtifactState.APPROVED,
    ],
)
def test_active_states_are_not_historical(state: ArtifactState) -> None:
    assert not is_historical_artifact_state(state)


def test_only_approved_is_state_level_integration_eligible() -> None:
    assert INTEGRATION_ELIGIBLE_ARTIFACT_STATES == frozenset(
        {ArtifactState.APPROVED}
    )
    for state in ArtifactState:
        assert is_integration_eligible_artifact_state(state) is (
            state is ArtifactState.APPROVED
        )


def test_approved_is_not_declared_current_by_this_contract() -> None:
    # C1.3 only expresses state-level eligibility. Phase 4 lineage/dependency
    # validation determines whether an APPROVED artifact is still current.
    assert can_transition_artifact_state(
        ArtifactState.APPROVED, ArtifactState.STALE
    )
    assert can_transition_artifact_state(
        ArtifactState.APPROVED, ArtifactState.SUPERSEDED
    )


def test_allowed_transition_sets_are_immutable() -> None:
    transitions = allowed_artifact_transitions(ArtifactState.DRAFT)
    assert isinstance(transitions, frozenset)
    with pytest.raises(AttributeError):
        transitions.add(ArtifactState.APPROVED)  # type: ignore[attr-defined]
