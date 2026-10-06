from __future__ import annotations

from enum import Enum
from types import MappingProxyType
from typing import Mapping


TASK_STATE_CONTRACT_VERSION = "1.0"


class TaskStateError(ValueError):
    """Base error for the Phase-1 task-state contract."""


class InvalidTaskStateTransition(TaskStateError):
    """Raised when a task-state transition is not allowed by the contract."""


class TaskState(str, Enum):
    """Canonical persisted states for one engineering task."""

    CREATED = "CREATED"
    BLOCKED = "BLOCKED"
    READY = "READY"
    QUEUED = "QUEUED"
    ASSIGNED = "ASSIGNED"
    RUNNING = "RUNNING"
    RESULT_PROPOSED = "RESULT_PROPOSED"
    REVIEWING = "REVIEWING"
    VALIDATING = "VALIDATING"
    DONE = "DONE"
    REWORK = "REWORK"
    FAILED = "FAILED"
    INTERRUPTED = "INTERRUPTED"
    NEEDS_HUMAN = "NEEDS_HUMAN"
    CANCELLED = "CANCELLED"


# DONE/FAILED/CANCELLED are final outcomes for one task record.
# NEEDS_HUMAN deliberately is not terminal: automation must stop there, but an
# explicit human decision may later return the task to BLOCKED/READY or close it.
TERMINAL_TASK_STATES = frozenset(
    {
        TaskState.DONE,
        TaskState.FAILED,
        TaskState.CANCELLED,
    }
)

HUMAN_GATE_TASK_STATES = frozenset({TaskState.NEEDS_HUMAN})


_ALLOWED_TRANSITIONS: Mapping[TaskState, frozenset[TaskState]] = MappingProxyType(
    {
        TaskState.CREATED: frozenset(
            {
                TaskState.BLOCKED,
                TaskState.READY,
                TaskState.CANCELLED,
            }
        ),
        TaskState.BLOCKED: frozenset(
            {
                TaskState.READY,
                TaskState.FAILED,
                TaskState.NEEDS_HUMAN,
                TaskState.CANCELLED,
            }
        ),
        TaskState.READY: frozenset(
            {
                TaskState.BLOCKED,
                TaskState.QUEUED,
                TaskState.CANCELLED,
            }
        ),
        TaskState.QUEUED: frozenset(
            {
                TaskState.READY,
                TaskState.ASSIGNED,
                TaskState.CANCELLED,
            }
        ),
        TaskState.ASSIGNED: frozenset(
            {
                TaskState.READY,
                TaskState.RUNNING,
                TaskState.INTERRUPTED,
                TaskState.CANCELLED,
            }
        ),
        TaskState.RUNNING: frozenset(
            {
                TaskState.RESULT_PROPOSED,
                TaskState.FAILED,
                TaskState.INTERRUPTED,
                TaskState.NEEDS_HUMAN,
                TaskState.CANCELLED,
            }
        ),
        # review_required is a task field in the frozen baseline, therefore a
        # RESULT_PROPOSED task may enter REVIEWING or go directly to VALIDATING.
        TaskState.RESULT_PROPOSED: frozenset(
            {
                TaskState.REVIEWING,
                TaskState.VALIDATING,
                TaskState.REWORK,
                TaskState.FAILED,
                TaskState.NEEDS_HUMAN,
                TaskState.CANCELLED,
            }
        ),
        TaskState.REVIEWING: frozenset(
            {
                TaskState.VALIDATING,
                TaskState.REWORK,
                TaskState.FAILED,
                TaskState.NEEDS_HUMAN,
                TaskState.CANCELLED,
            }
        ),
        TaskState.VALIDATING: frozenset(
            {
                TaskState.DONE,
                TaskState.REWORK,
                TaskState.FAILED,
                TaskState.NEEDS_HUMAN,
                TaskState.CANCELLED,
            }
        ),
        TaskState.REWORK: frozenset(
            {
                TaskState.BLOCKED,
                TaskState.READY,
                TaskState.FAILED,
                TaskState.NEEDS_HUMAN,
                TaskState.CANCELLED,
            }
        ),
        TaskState.INTERRUPTED: frozenset(
            {
                TaskState.BLOCKED,
                TaskState.READY,
                TaskState.FAILED,
                TaskState.NEEDS_HUMAN,
                TaskState.CANCELLED,
            }
        ),
        TaskState.NEEDS_HUMAN: frozenset(
            {
                TaskState.BLOCKED,
                TaskState.READY,
                TaskState.FAILED,
                TaskState.CANCELLED,
            }
        ),
        TaskState.DONE: frozenset(),
        TaskState.FAILED: frozenset(),
        TaskState.CANCELLED: frozenset(),
    }
)


def parse_task_state(raw: TaskState | str) -> TaskState:
    """Parse one canonical state without silently normalizing user data."""

    if isinstance(raw, TaskState):
        return raw
    if not isinstance(raw, str):
        raise TaskStateError("task state must be a string or TaskState")
    if not raw:
        raise TaskStateError("task state must not be empty")
    if raw != raw.strip() or any(ch.isspace() for ch in raw):
        raise TaskStateError("task state must not contain whitespace")
    try:
        return TaskState(raw)
    except ValueError as exc:
        raise TaskStateError(f"unknown task state: {raw!r}") from exc


def serialize_task_state(state: TaskState | str) -> str:
    """Serialize a canonical state to its stable persisted wire value."""

    return parse_task_state(state).value


def allowed_task_transitions(state: TaskState | str) -> frozenset[TaskState]:
    """Return the immutable set of legal direct successor states."""

    canonical = parse_task_state(state)
    return _ALLOWED_TRANSITIONS[canonical]


def can_transition_task_state(
    current: TaskState | str,
    target: TaskState | str,
) -> bool:
    current_state = parse_task_state(current)
    target_state = parse_task_state(target)
    return target_state in _ALLOWED_TRANSITIONS[current_state]


def require_task_transition(
    current: TaskState | str,
    target: TaskState | str,
) -> TaskState:
    """Validate one direct transition and return the canonical target state."""

    current_state = parse_task_state(current)
    target_state = parse_task_state(target)
    if target_state not in _ALLOWED_TRANSITIONS[current_state]:
        raise InvalidTaskStateTransition(
            f"invalid task transition: {current_state.value} -> {target_state.value}"
        )
    return target_state


def is_terminal_task_state(state: TaskState | str) -> bool:
    return parse_task_state(state) in TERMINAL_TASK_STATES


def is_human_gate_task_state(state: TaskState | str) -> bool:
    return parse_task_state(state) in HUMAN_GATE_TASK_STATES


def recover_task_state_after_restart(state: TaskState | str) -> TaskState:
    """Apply the frozen crash-recovery rule.

    The baseline requires a task persisted as RUNNING during an unexpected
    process stop to recover as INTERRUPTED. Other states are unchanged here;
    later persistence/scheduler phases own attempt creation and rescheduling.
    """

    canonical = parse_task_state(state)
    if canonical is TaskState.RUNNING:
        return TaskState.INTERRUPTED
    return canonical
