from __future__ import annotations

import json

import pytest

from ai.contracts.task_states import (
    HUMAN_GATE_TASK_STATES,
    TASK_STATE_CONTRACT_VERSION,
    TERMINAL_TASK_STATES,
    InvalidTaskStateTransition,
    TaskState,
    TaskStateError,
    allowed_task_transitions,
    can_transition_task_state,
    is_human_gate_task_state,
    is_terminal_task_state,
    parse_task_state,
    recover_task_state_after_restart,
    require_task_transition,
    serialize_task_state,
)


EXPECTED_STATES = [
    "CREATED",
    "BLOCKED",
    "READY",
    "QUEUED",
    "ASSIGNED",
    "RUNNING",
    "RESULT_PROPOSED",
    "REVIEWING",
    "VALIDATING",
    "DONE",
    "REWORK",
    "FAILED",
    "INTERRUPTED",
    "NEEDS_HUMAN",
    "CANCELLED",
]


def test_contract_version_is_explicit() -> None:
    assert TASK_STATE_CONTRACT_VERSION == "1.0"


def test_frozen_baseline_state_set_and_order_are_exact() -> None:
    assert [state.value for state in TaskState] == EXPECTED_STATES


@pytest.mark.parametrize("raw", EXPECTED_STATES)
def test_parse_and_serialize_round_trip(raw: str) -> None:
    state = parse_task_state(raw)
    wire = json.loads(json.dumps({"state": serialize_task_state(state)}))
    assert wire == {"state": raw}
    assert parse_task_state(wire["state"]) is state


@pytest.mark.parametrize(
    "raw",
    [
        "",
        " ",
        "RUNNING ",
        " RUNNING",
        "runNING",
        "running",
        "RESULT PROPOSED",
        "UNKNOWN",
        1,
        None,
    ],
)
def test_parse_rejects_noncanonical_states(raw: object) -> None:
    with pytest.raises(TaskStateError):
        parse_task_state(raw)  # type: ignore[arg-type]


def test_normal_reviewed_path_is_legal() -> None:
    path = [
        TaskState.CREATED,
        TaskState.READY,
        TaskState.QUEUED,
        TaskState.ASSIGNED,
        TaskState.RUNNING,
        TaskState.RESULT_PROPOSED,
        TaskState.REVIEWING,
        TaskState.VALIDATING,
        TaskState.DONE,
    ]
    for current, target in zip(path, path[1:]):
        assert require_task_transition(current, target) is target


def test_review_can_be_skipped_when_task_contract_does_not_require_it() -> None:
    assert can_transition_task_state(
        TaskState.RESULT_PROPOSED, TaskState.VALIDATING
    )


def test_blocked_and_rework_paths_return_to_scheduler_ready_states() -> None:
    assert can_transition_task_state(TaskState.CREATED, TaskState.BLOCKED)
    assert can_transition_task_state(TaskState.BLOCKED, TaskState.READY)
    assert can_transition_task_state(TaskState.VALIDATING, TaskState.REWORK)
    assert can_transition_task_state(TaskState.REWORK, TaskState.READY)
    assert can_transition_task_state(TaskState.REWORK, TaskState.BLOCKED)


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (TaskState.CREATED, TaskState.RUNNING),
        (TaskState.READY, TaskState.DONE),
        (TaskState.RUNNING, TaskState.DONE),
        (TaskState.REVIEWING, TaskState.DONE),
        (TaskState.DONE, TaskState.READY),
        (TaskState.FAILED, TaskState.READY),
        (TaskState.CANCELLED, TaskState.READY),
        (TaskState.RUNNING, TaskState.RUNNING),
    ],
)
def test_invalid_direct_transitions_are_rejected(
    current: TaskState, target: TaskState
) -> None:
    assert not can_transition_task_state(current, target)
    with pytest.raises(
        InvalidTaskStateTransition,
        match=rf"{current.value} -> {target.value}",
    ):
        require_task_transition(current, target)


def test_terminal_states_are_final_outcomes() -> None:
    assert TERMINAL_TASK_STATES == frozenset(
        {TaskState.DONE, TaskState.FAILED, TaskState.CANCELLED}
    )
    for state in TERMINAL_TASK_STATES:
        assert is_terminal_task_state(state)
        assert allowed_task_transitions(state) == frozenset()


def test_needs_human_is_an_automation_halt_but_not_a_final_outcome() -> None:
    assert HUMAN_GATE_TASK_STATES == frozenset({TaskState.NEEDS_HUMAN})
    assert is_human_gate_task_state(TaskState.NEEDS_HUMAN)
    assert not is_terminal_task_state(TaskState.NEEDS_HUMAN)
    assert can_transition_task_state(TaskState.NEEDS_HUMAN, TaskState.READY)
    assert can_transition_task_state(TaskState.NEEDS_HUMAN, TaskState.BLOCKED)
    assert can_transition_task_state(TaskState.NEEDS_HUMAN, TaskState.CANCELLED)


def test_running_recovers_as_interrupted_after_restart() -> None:
    assert recover_task_state_after_restart(TaskState.RUNNING) is TaskState.INTERRUPTED


@pytest.mark.parametrize(
    "state",
    [state for state in TaskState if state is not TaskState.RUNNING],
)
def test_restart_recovery_does_not_invent_other_state_changes(state: TaskState) -> None:
    assert recover_task_state_after_restart(state) is state


def test_interrupted_can_be_rescheduled_without_resuming_partial_stream() -> None:
    assert can_transition_task_state(TaskState.INTERRUPTED, TaskState.READY)
    assert can_transition_task_state(TaskState.INTERRUPTED, TaskState.BLOCKED)
    assert not can_transition_task_state(TaskState.INTERRUPTED, TaskState.RUNNING)


def test_allowed_transition_sets_are_immutable() -> None:
    transitions = allowed_task_transitions(TaskState.RUNNING)
    assert isinstance(transitions, frozenset)
    with pytest.raises(AttributeError):
        transitions.add(TaskState.DONE)  # type: ignore[attr-defined]
