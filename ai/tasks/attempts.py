from __future__ import annotations

from dataclasses import dataclass

from ai.contracts.ids import CanonicalId, CanonicalIdError, IdKind
from ai.contracts.task_states import TaskState
from ai.project.store import ProjectStore
from ai.tasks.lifecycle import (
    TaskLifecycleStore,
    TaskTransitionConflictError,
)
from ai.tasks.schema import TaskRecord
from ai.tasks.store import TaskStore


TASK_ATTEMPT_CONTRACT_VERSION = "1.0"


class TaskAttemptError(RuntimeError):
    """Base error for deterministic T5.7 attempt-budget behavior."""


class TaskAttemptConflictError(TaskAttemptError):
    """Raised when an attempt budget cannot be evaluated or routed safely."""


@dataclass(frozen=True)
class TaskAttemptBudget:
    task_id: CanonicalId
    attempt: int
    max_attempts: int
    remaining_attempts: int
    exhausted: bool

    def __post_init__(self) -> None:
        task = self.task_id
        if not isinstance(task, CanonicalId):
            try:
                task = CanonicalId.parse(task)
            except (CanonicalIdError, TypeError) as exc:
                raise TaskAttemptConflictError(
                    "attempt budget task_id must be canonical"
                ) from exc
            object.__setattr__(self, "task_id", task)

        if task.kind is not IdKind.TASK:
            raise TaskAttemptConflictError(
                "attempt budget task_id must identify a task"
            )

        for field in ("attempt", "max_attempts", "remaining_attempts"):
            value = getattr(self, field)
            if isinstance(value, bool) or not isinstance(value, int):
                raise TaskAttemptConflictError(
                    f"attempt budget {field} must be an integer"
                )

        if self.max_attempts < 1:
            raise TaskAttemptConflictError(
                "attempt budget max_attempts must be positive"
            )
        if not 0 <= self.attempt <= self.max_attempts:
            raise TaskAttemptConflictError(
                "attempt budget attempt is outside bounds"
            )
        if self.remaining_attempts != self.max_attempts - self.attempt:
            raise TaskAttemptConflictError(
                "attempt budget remaining count is inconsistent"
            )
        if self.exhausted is not (self.remaining_attempts == 0):
            raise TaskAttemptConflictError(
                "attempt budget exhausted flag is inconsistent"
            )


@dataclass(frozen=True)
class TaskReworkRoute:
    task_id: CanonicalId
    attempt: int
    max_attempts: int
    target_status: TaskState

    def __post_init__(self) -> None:
        if self.target_status not in {
            TaskState.BLOCKED,
            TaskState.NEEDS_HUMAN,
        }:
            raise TaskAttemptConflictError(
                "rework route target must be BLOCKED or NEEDS_HUMAN"
            )


class TaskAttemptStore:
    """Read attempt budgets and route REWORK without unbounded retries.

    Attempt increment itself is owned by TaskLifecycleStore after T5.7:
    the only transition into RUNNING is ASSIGNED -> RUNNING, and that
    transition atomically increments `attempt` by exactly one.

    Rework routing follows the frozen anti-loop policy:

      REWORK + remaining attempt budget -> BLOCKED
      REWORK + exhausted budget         -> NEEDS_HUMAN

    BLOCKED is intentional: T5.5 must re-evaluate dependencies before another
    retry can become READY. T5.7 does not enqueue, schedule, sleep, retry in a
    loop, apply timeout, or recover INTERRUPTED work.
    """

    def __init__(self, project_store: ProjectStore) -> None:
        if not isinstance(project_store, ProjectStore):
            raise TypeError("project_store must be ProjectStore")
        self._tasks = TaskStore(project_store)
        self._lifecycle = TaskLifecycleStore(project_store)

    @staticmethod
    def _budget_from_record(record: TaskRecord) -> TaskAttemptBudget:
        remaining = record.max_attempts - record.attempt
        return TaskAttemptBudget(
            task_id=record.task_id,
            attempt=record.attempt,
            max_attempts=record.max_attempts,
            remaining_attempts=remaining,
            exhausted=(remaining == 0),
        )

    def budget(
        self,
        project_id: CanonicalId | str,
        task_id: CanonicalId | str,
    ) -> TaskAttemptBudget:
        record = self._tasks.open_task(project_id, task_id)
        return self._budget_from_record(record)

    def route_rework(
        self,
        project_id: CanonicalId | str,
        task_id: CanonicalId | str,
    ) -> TaskReworkRoute:
        current = self._tasks.open_task(project_id, task_id)
        if current.status is not TaskState.REWORK:
            raise TaskAttemptConflictError(
                "attempt-budget rework routing requires REWORK status"
            )

        budget = self._budget_from_record(current)
        target = (
            TaskState.NEEDS_HUMAN
            if budget.exhausted
            else TaskState.BLOCKED
        )

        try:
            updated = self._lifecycle.transition_task(
                project_id,
                current.task_id,
                target,
            )
        except TaskTransitionConflictError as exc:
            raise TaskAttemptConflictError(
                f"rework could not be routed by attempt budget: {exc}"
            ) from exc

        if updated.attempt != current.attempt:
            raise TaskAttemptConflictError(
                "rework routing unexpectedly changed attempt"
            )

        return TaskReworkRoute(
            task_id=current.task_id,
            attempt=current.attempt,
            max_attempts=current.max_attempts,
            target_status=updated.status,
        )
