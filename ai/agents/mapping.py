from __future__ import annotations

from dataclasses import dataclass

from ai.agents.instance import (
    AgentInstance,
    AgentInstanceFactory,
    AgentInstanceRoleError,
)
from ai.agents.roles import (
    DEFAULT_ROLE_REGISTRY,
    RoleRegistry,
)
from ai.contracts.ids import CanonicalId, CanonicalIdError, IdKind
from ai.project.store import ProjectStore
from ai.tasks.schema import TaskRecord
from ai.tasks.store import TaskNotFoundError, TaskStore, TaskStoreConflictError


TASK_AGENT_MAPPING_CONTRACT_VERSION = "1.0"


class TaskAgentMappingError(RuntimeError):
    """Base error for deterministic G6.3 Task -> Agent mapping."""


class TaskAgentMappingConflictError(TaskAgentMappingError):
    """Raised when a persisted task cannot be mapped safely."""


class TaskAgentUnknownRoleError(TaskAgentMappingError):
    """Raised when TaskRecord.role is not enabled by the current RoleRegistry."""


@dataclass(frozen=True)
class TaskAgentBinding:
    """Immutable logical binding between one persisted task and one agent.

    G6.3 binds identity only. It does not execute the task, build context,
    select a model, call a provider, mutate Task State, or persist an Agent run.
    """

    project_id: CanonicalId
    task_id: CanonicalId
    role_id: str
    agent: AgentInstance

    def __post_init__(self) -> None:
        project = self.project_id
        task = self.task_id

        if not isinstance(project, CanonicalId):
            try:
                project = CanonicalId.parse(project)
            except (CanonicalIdError, TypeError) as exc:
                raise TaskAgentMappingConflictError(
                    "project_id must be canonical"
                ) from exc
            object.__setattr__(self, "project_id", project)

        if not isinstance(task, CanonicalId):
            try:
                task = CanonicalId.parse(task)
            except (CanonicalIdError, TypeError) as exc:
                raise TaskAgentMappingConflictError(
                    "task_id must be canonical"
                ) from exc
            object.__setattr__(self, "task_id", task)

        if project.kind is not IdKind.PROJECT:
            raise TaskAgentMappingConflictError(
                "project_id must identify a project"
            )
        if task.kind is not IdKind.TASK:
            raise TaskAgentMappingConflictError(
                "task_id must identify a task"
            )
        if not isinstance(self.agent, AgentInstance):
            raise TaskAgentMappingConflictError(
                "agent must be AgentInstance"
            )
        if self.role_id != self.agent.role_id:
            raise TaskAgentMappingConflictError(
                "binding role_id does not match agent role"
            )
        if self.agent.instance_key != task.value:
            raise TaskAgentMappingConflictError(
                "agent instance_key must equal canonical task_id"
            )


class TaskAgentMapper:
    """Map one persisted TaskRecord to one logical G6.2 AgentInstance.

    Deterministic mapping rule:

        TaskRecord.role
              +
        TaskRecord.task_id
              ↓
        AgentInstance(
            role = RoleRegistry[TaskRecord.role],
            instance_key = TaskRecord.task_id
        )

    The persisted task record remains source of truth for role and task
    identity. G6.3 does not infer a role from title, schema, validators, or
    natural-language content.
    """

    def __init__(
        self,
        project_store: ProjectStore,
        role_registry: RoleRegistry = DEFAULT_ROLE_REGISTRY,
    ) -> None:
        if not isinstance(project_store, ProjectStore):
            raise TypeError("project_store must be ProjectStore")
        if not isinstance(role_registry, RoleRegistry):
            raise TypeError("role_registry must be RoleRegistry")

        self._tasks = TaskStore(project_store)
        self._role_registry = role_registry
        self._factory = AgentInstanceFactory(role_registry)

    @property
    def role_registry(self) -> RoleRegistry:
        return self._role_registry

    @staticmethod
    def _validate_task_record(task: TaskRecord) -> TaskRecord:
        if not isinstance(task, TaskRecord):
            raise TaskAgentMappingConflictError(
                "task must be TaskRecord"
            )
        return task

    def map_record(self, task: TaskRecord) -> TaskAgentBinding:
        record = self._validate_task_record(task)

        if not self._role_registry.contains(record.role):
            raise TaskAgentUnknownRoleError(
                "task role is not enabled by the Phase-6 MVP RoleRegistry: "
                f"{record.role}"
            )

        try:
            agent = self._factory.create(
                instance_key=record.task_id.value,
                role_id=record.role,
            )
        except AgentInstanceRoleError as exc:
            raise TaskAgentUnknownRoleError(
                f"task role cannot create AgentInstance: {record.role}"
            ) from exc

        return TaskAgentBinding(
            project_id=record.project_id,
            task_id=record.task_id,
            role_id=record.role,
            agent=agent,
        )

    def map_task(
        self,
        project_id: CanonicalId | str,
        task_id: CanonicalId | str,
    ) -> TaskAgentBinding:
        try:
            record = self._tasks.open_task(
                project_id,
                task_id,
            )
        except (TaskNotFoundError, TaskStoreConflictError) as exc:
            raise TaskAgentMappingConflictError(
                f"persisted task cannot be opened safely: {exc}"
            ) from exc

        return self.map_record(record)
