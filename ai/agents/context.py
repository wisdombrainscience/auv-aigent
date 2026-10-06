from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any, Iterable

from ai.agents.mapping import (
    TaskAgentBinding,
    TaskAgentMapper,
    TaskAgentMappingError,
)
from ai.agents.roles import RoleDefinition
from ai.artifacts.hashing import (
    ArtifactContentStore,
    ArtifactHashError,
)
from ai.artifacts.schema import ArtifactRecord
from ai.artifacts.versions import (
    ArtifactVersionNotFoundError,
    ArtifactVersionStore,
    ArtifactVersionStoreError,
)
from ai.contracts.artifact_states import ArtifactState
from ai.contracts.ids import IdKind
from ai.project.facts import (
    Fact,
    FactSetNotFoundError,
    FactStore,
    FactStoreError,
)
from ai.project.requirement_versions import (
    RequirementVersionNotFoundError,
    RequirementVersionStore,
    RequirementVersionError,
)
from ai.project.requirements import (
    Requirement,
    RequirementSetStatus,
)
from ai.project.store import ProjectStore
from ai.tasks.schema import TaskRecord
from ai.tasks.store import TaskStore


MINIMAL_CONTEXT_CONTRACT_VERSION = "1.0"

# Operational bounds only; they do not alter project/task semantic limits.
MAX_CONTEXT_FACT_KEYS = 64
MAX_CONTEXT_ARTIFACT_TEXT_BYTES = 256 * 1024
MAX_MINIMAL_CONTEXT_BYTES = 1024 * 1024

CONTEXT_POLICY_HIERARCHY = (
    "SYSTEM_POLICY",
    "ROLE_POLICY",
    "TASK_CONTRACT",
    "APPROVED_PROJECT_STATE",
    "RETRIEVED_DOCUMENT_CONTENT",
)


class MinimalContextError(RuntimeError):
    """Base error for deterministic G6.4 context construction."""


class MinimalContextConflictError(MinimalContextError):
    """Raised when persisted context inputs are missing/inconsistent/untrusted."""


class MinimalContextLimitError(MinimalContextError):
    """Raised when bounded minimal context would exceed operational limits."""


@dataclass(frozen=True)
class ApprovedRequirementContext:
    requirement_id: str
    version: int
    text: str
    provenance: str

    @classmethod
    def from_requirement(
        cls,
        requirement: Requirement,
        *,
        version: int,
    ) -> "ApprovedRequirementContext":
        return cls(
            requirement_id=requirement.requirement_id.value,
            version=version,
            text=requirement.text,
            provenance=requirement.provenance.value,
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "requirement_id": self.requirement_id,
            "version": self.version,
            "text": self.text,
            "provenance": self.provenance,
        }


@dataclass(frozen=True)
class ApprovedArtifactContext:
    artifact_id: str
    version: int
    type: str
    producer_task: str
    hash: str
    content_text: str

    @classmethod
    def from_record(
        cls,
        record: ArtifactRecord,
        *,
        content_text: str,
    ) -> "ApprovedArtifactContext":
        if record.status is not ArtifactState.APPROVED:
            raise MinimalContextConflictError(
                "parent artifact must be APPROVED"
            )
        return cls(
            artifact_id=record.artifact_id.value,
            version=record.version,
            type=record.type,
            producer_task=record.producer_task.value,
            hash=record.hash,
            content_text=content_text,
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "artifact_id": self.artifact_id,
            "version": self.version,
            "type": self.type,
            "producer_task": self.producer_task,
            "hash": self.hash,
            "content_text": self.content_text,
        }


@dataclass(frozen=True)
class MinimalAgentContext:
    """Structured, prompt-free context selected for one mapped task.

    The bundle intentionally keeps policy/project sources in separate fields so
    later prompt assembly cannot silently treat project content as role/system
    policy. Retrieved documents, review issues and deterministic validation
    failures are not yet wired in G6.4 and are therefore absent rather than
    fabricated.
    """

    binding: TaskAgentBinding
    role_policy: RoleDefinition
    task_contract: TaskRecord
    approved_requirements: tuple[ApprovedRequirementContext, ...]
    approved_parent_artifacts: tuple[ApprovedArtifactContext, ...]
    project_facts: tuple[Fact, ...]
    encoded_size_bytes: int

    def __post_init__(self) -> None:
        if not isinstance(self.binding, TaskAgentBinding):
            raise MinimalContextConflictError(
                "binding must be TaskAgentBinding"
            )
        if not isinstance(self.role_policy, RoleDefinition):
            raise MinimalContextConflictError(
                "role_policy must be RoleDefinition"
            )
        if not isinstance(self.task_contract, TaskRecord):
            raise MinimalContextConflictError(
                "task_contract must be TaskRecord"
            )
        if self.binding.task_id != self.task_contract.task_id:
            raise MinimalContextConflictError(
                "binding/task identity mismatch"
            )
        if self.binding.project_id != self.task_contract.project_id:
            raise MinimalContextConflictError(
                "binding/task project mismatch"
            )
        if self.binding.role_id != self.task_contract.role:
            raise MinimalContextConflictError(
                "binding/task role mismatch"
            )
        if self.role_policy.role_id != self.binding.role_id:
            raise MinimalContextConflictError(
                "role policy does not match binding role"
            )
        if (
            isinstance(self.encoded_size_bytes, bool)
            or not isinstance(self.encoded_size_bytes, int)
            or self.encoded_size_bytes < 0
            or self.encoded_size_bytes > MAX_MINIMAL_CONTEXT_BYTES
        ):
            raise MinimalContextLimitError(
                "encoded minimal context size is outside bounds"
            )

    def to_dict(self) -> dict[str, object]:
        return {
            "contract_version": MINIMAL_CONTEXT_CONTRACT_VERSION,
            "policy_hierarchy": list(CONTEXT_POLICY_HIERARCHY),
            "binding": {
                "project_id": self.binding.project_id.value,
                "task_id": self.binding.task_id.value,
                "role_id": self.binding.role_id,
                "agent_identity": self.binding.agent.logical_identity,
            },
            "role_policy": {
                "role_id": self.role_policy.role_id,
                "title": self.role_policy.title,
                "purpose": self.role_policy.purpose,
            },
            "task_contract": self.task_contract.to_dict(),
            "approved_requirements": [
                item.to_dict() for item in self.approved_requirements
            ],
            "approved_parent_artifacts": [
                item.to_dict() for item in self.approved_parent_artifacts
            ],
            "project_facts": [
                item.to_dict() for item in self.project_facts
            ],
            "reserved_context_sections": {
                "previous_review_issues": [],
                "previous_validation_failures": [],
                "retrieved_documents": [],
            },
            "encoded_size_bytes": self.encoded_size_bytes,
        }


class MinimalContextBuilder:
    """Build bounded context only from trusted persisted project/task inputs."""

    def __init__(self, project_store: ProjectStore) -> None:
        if not isinstance(project_store, ProjectStore):
            raise TypeError("project_store must be ProjectStore")
        self._projects = project_store
        self._tasks = TaskStore(project_store)
        self._mapper = TaskAgentMapper(project_store)
        self._requirements = RequirementVersionStore(project_store)
        self._artifacts = ArtifactVersionStore(project_store)
        self._artifact_content = ArtifactContentStore(project_store)
        self._facts = FactStore(project_store)

    @staticmethod
    def _fact_keys(raw: Iterable[str]) -> tuple[str, ...]:
        try:
            values = tuple(raw)
        except TypeError as exc:
            raise MinimalContextConflictError(
                "fact_keys must be iterable"
            ) from exc
        if len(values) > MAX_CONTEXT_FACT_KEYS:
            raise MinimalContextLimitError(
                "fact_keys exceeds MAX_CONTEXT_FACT_KEYS"
            )
        if any(not isinstance(item, str) or not item for item in values):
            raise MinimalContextConflictError(
                "fact_keys entries must be non-empty strings"
            )
        if len(set(values)) != len(values):
            raise MinimalContextConflictError(
                "fact_keys must not contain duplicates"
            )
        # Fact order in context is canonical rather than caller-order dependent.
        return tuple(sorted(values))

    def _approved_requirements(
        self,
        task: TaskRecord,
    ) -> tuple[ApprovedRequirementContext, ...]:
        result: list[ApprovedRequirementContext] = []
        for reference in task.input_artifacts:
            if reference.entity_id.kind is not IdKind.REQUIREMENT:
                continue
            try:
                snapshot = self._requirements.open_version(
                    task.project_id,
                    reference.version,
                )
            except (
                RequirementVersionNotFoundError,
                RequirementVersionError,
            ) as exc:
                raise MinimalContextConflictError(
                    "referenced requirement version cannot be opened"
                ) from exc

            if snapshot.requirement_set.status is not RequirementSetStatus.APPROVED:
                raise MinimalContextConflictError(
                    "referenced requirement version is not APPROVED"
                )

            requirement = next(
                (
                    item
                    for item in snapshot.requirement_set.requirements
                    if item.requirement_id == reference.entity_id
                ),
                None,
            )
            if requirement is None:
                raise MinimalContextConflictError(
                    "referenced requirement ID is absent from requested version"
                )
            result.append(
                ApprovedRequirementContext.from_requirement(
                    requirement,
                    version=reference.version,
                )
            )
        return tuple(result)

    def _approved_artifacts(
        self,
        task: TaskRecord,
    ) -> tuple[ApprovedArtifactContext, ...]:
        result: list[ApprovedArtifactContext] = []
        for reference in task.input_artifacts:
            if reference.entity_id.kind is not IdKind.ARTIFACT:
                continue
            try:
                record = self._artifacts.open_version(
                    task.project_id,
                    reference.entity_id,
                    reference.version,
                )
            except (
                ArtifactVersionNotFoundError,
                ArtifactVersionStoreError,
            ) as exc:
                raise MinimalContextConflictError(
                    "referenced artifact version cannot be opened"
                ) from exc

            if record.status is not ArtifactState.APPROVED:
                raise MinimalContextConflictError(
                    "referenced parent artifact is not APPROVED"
                )

            try:
                raw = self._artifact_content.read(
                    task.project_id,
                    record.hash,
                )
            except ArtifactHashError as exc:
                raise MinimalContextConflictError(
                    "referenced artifact content failed integrity verification"
                ) from exc

            if len(raw) > MAX_CONTEXT_ARTIFACT_TEXT_BYTES:
                raise MinimalContextLimitError(
                    "parent artifact content exceeds "
                    "MAX_CONTEXT_ARTIFACT_TEXT_BYTES"
                )
            try:
                content_text = raw.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise MinimalContextConflictError(
                    "minimal context supports UTF-8 text parent artifacts only"
                ) from exc
            if "\x00" in content_text:
                raise MinimalContextConflictError(
                    "parent artifact text must not contain NUL"
                )

            result.append(
                ApprovedArtifactContext.from_record(
                    record,
                    content_text=content_text,
                )
            )
        return tuple(result)

    def _selected_facts(
        self,
        project_id,
        *,
        fact_keys: tuple[str, ...],
    ) -> tuple[Fact, ...]:
        if not fact_keys:
            return ()
        try:
            fact_set = self._facts.open(project_id)
        except (FactSetNotFoundError, FactStoreError) as exc:
            raise MinimalContextConflictError(
                "requested project facts cannot be opened"
            ) from exc
        by_key = {item.key: item for item in fact_set.facts}
        missing = [key for key in fact_keys if key not in by_key]
        if missing:
            raise MinimalContextConflictError(
                "requested project fact is missing: " + ",".join(missing)
            )
        return tuple(by_key[key] for key in fact_keys)

    @staticmethod
    def _encoded_size(payload: dict[str, object]) -> int:
        try:
            raw = json.dumps(
                payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
        except (TypeError, ValueError) as exc:
            raise MinimalContextConflictError(
                "minimal context cannot be canonically encoded"
            ) from exc
        if len(raw) > MAX_MINIMAL_CONTEXT_BYTES:
            raise MinimalContextLimitError(
                "minimal context exceeds MAX_MINIMAL_CONTEXT_BYTES"
            )
        return len(raw)

    def build(
        self,
        project_id,
        task_id,
        *,
        fact_keys: Iterable[str] = (),
    ) -> MinimalAgentContext:
        keys = self._fact_keys(fact_keys)
        try:
            binding = self._mapper.map_task(project_id, task_id)
        except TaskAgentMappingError as exc:
            raise MinimalContextConflictError(
                f"task-to-agent mapping failed: {exc}"
            ) from exc

        task = self._tasks.open_task(
            binding.project_id,
            binding.task_id,
        )
        requirements = self._approved_requirements(task)
        artifacts = self._approved_artifacts(task)
        facts = self._selected_facts(
            task.project_id,
            fact_keys=keys,
        )

        provisional = {
            "contract_version": MINIMAL_CONTEXT_CONTRACT_VERSION,
            "policy_hierarchy": list(CONTEXT_POLICY_HIERARCHY),
            "binding": {
                "project_id": binding.project_id.value,
                "task_id": binding.task_id.value,
                "role_id": binding.role_id,
                "agent_identity": binding.agent.logical_identity,
            },
            "role_policy": {
                "role_id": binding.agent.role.role_id,
                "title": binding.agent.role.title,
                "purpose": binding.agent.role.purpose,
            },
            "task_contract": task.to_dict(),
            "approved_requirements": [item.to_dict() for item in requirements],
            "approved_parent_artifacts": [item.to_dict() for item in artifacts],
            "project_facts": [item.to_dict() for item in facts],
            "reserved_context_sections": {
                "previous_review_issues": [],
                "previous_validation_failures": [],
                "retrieved_documents": [],
            },
        }
        size = self._encoded_size(provisional)

        return MinimalAgentContext(
            binding=binding,
            role_policy=binding.agent.role,
            task_contract=task,
            approved_requirements=requirements,
            approved_parent_artifacts=artifacts,
            project_facts=facts,
            encoded_size_bytes=size,
        )
