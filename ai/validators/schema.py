from __future__ import annotations

from dataclasses import dataclass
import json
from types import MappingProxyType
from typing import Any, Callable, Mapping

from ai.agents.draft_artifact import (
    MAX_RESULT_DRAFT_ARTIFACT_BYTES,
    RESULT_DRAFT_ARTIFACT_TYPE,
    ResultDraftArtifactConflictError,
    ResultDraftArtifactPayload,
)
from ai.artifacts.hashing import (
    ArtifactContentStore,
    ArtifactHashError,
)
from ai.artifacts.versions import (
    ArtifactVersionConflictError,
    ArtifactVersionNotFoundError,
    ArtifactVersionStore,
)
from ai.contracts.ids import CanonicalId
from ai.contracts.validation_result import (
    ValidationCheck,
    ValidationResult,
    ValidationStatus,
)
from ai.project.store import ProjectStore
from ai.tasks.store import (
    TaskNotFoundError,
    TaskStore,
    TaskStoreConflictError,
)


SCHEMA_VALIDATOR_CONTRACT_VERSION = "1.0"
SCHEMA_VALIDATOR_ID = "schema"
MAX_SCHEMA_VALIDATION_BYTES = MAX_RESULT_DRAFT_ARTIFACT_BYTES


class SchemaValidationError(RuntimeError):
    """Base error for V7.1 deterministic schema validation."""


class SchemaValidationConflictError(SchemaValidationError):
    """Raised when validation cannot safely bind to persisted project state."""


class SchemaRegistryError(SchemaValidationError):
    """Raised when the bounded V7.1 schema registry is malformed."""


class UnknownArtifactSchemaError(SchemaRegistryError):
    """Raised when no V7.1 schema is registered for an artifact type."""


def _clean_text(raw: Any, *, field: str) -> str:
    if not isinstance(raw, str) or not raw or raw != raw.strip():
        raise SchemaRegistryError(f"{field} must be clean non-empty text")
    return raw


def _strict_json_object(raw: bytes) -> Mapping[str, Any]:
    if not isinstance(raw, bytes):
        raise TypeError("raw must be bytes")
    if len(raw) > MAX_SCHEMA_VALIDATION_BYTES:
        raise ValueError("artifact content exceeds MAX_SCHEMA_VALIDATION_BYTES")

    def reject_constant(value: str) -> None:
        raise ValueError(f"non-finite JSON constant {value!r}")

    def no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate JSON key: {key}")
            result[key] = value
        return result

    try:
        parsed = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=no_duplicates,
            parse_constant=reject_constant,
        )
    except UnicodeDecodeError as exc:
        raise ValueError("artifact content is not UTF-8") from exc
    except json.JSONDecodeError as exc:
        raise ValueError("artifact content is invalid JSON") from exc

    if not isinstance(parsed, Mapping):
        raise ValueError("artifact content top level must be an object")
    return parsed


PayloadParser = Callable[[Mapping[str, Any]], object]


@dataclass(frozen=True)
class ArtifactSchemaDefinition:
    """One immutable V7.1 artifact-payload schema registration."""

    artifact_type: str
    contract_name: str
    parser: PayloadParser

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "artifact_type",
            _clean_text(self.artifact_type, field="artifact_type"),
        )
        object.__setattr__(
            self,
            "contract_name",
            _clean_text(self.contract_name, field="contract_name"),
        )
        if not callable(self.parser):
            raise SchemaRegistryError("parser must be callable")


class ArtifactSchemaRegistry:
    """Deterministic bounded lookup for V7.1 artifact payload schemas."""

    def __init__(self, definitions: tuple[ArtifactSchemaDefinition, ...]) -> None:
        if not isinstance(definitions, tuple):
            raise TypeError("definitions must be a tuple")
        if not definitions:
            raise SchemaRegistryError("at least one artifact schema is required")
        by_type: dict[str, ArtifactSchemaDefinition] = {}
        for definition in definitions:
            if not isinstance(definition, ArtifactSchemaDefinition):
                raise SchemaRegistryError(
                    "registry entries must be ArtifactSchemaDefinition"
                )
            if definition.artifact_type in by_type:
                raise SchemaRegistryError(
                    f"duplicate artifact schema: {definition.artifact_type}"
                )
            by_type[definition.artifact_type] = definition
        self._definitions = MappingProxyType(by_type)

    def artifact_types(self) -> tuple[str, ...]:
        return tuple(sorted(self._definitions))

    def contains(self, artifact_type: str) -> bool:
        if not isinstance(artifact_type, str):
            return False
        return artifact_type in self._definitions

    def get(self, artifact_type: str) -> ArtifactSchemaDefinition:
        canonical = _clean_text(artifact_type, field="artifact_type")
        try:
            return self._definitions[canonical]
        except KeyError as exc:
            raise UnknownArtifactSchemaError(
                f"no V7.1 schema is registered for artifact type {canonical!r}"
            ) from exc


def _parse_result_draft_payload(raw: Mapping[str, Any]) -> ResultDraftArtifactPayload:
    return ResultDraftArtifactPayload.from_dict(raw)


DEFAULT_ARTIFACT_SCHEMA_REGISTRY = ArtifactSchemaRegistry(
    (
        ArtifactSchemaDefinition(
            artifact_type=RESULT_DRAFT_ARTIFACT_TYPE,
            contract_name="ResultDraftArtifactPayload",
            parser=_parse_result_draft_payload,
        ),
    )
)


def _pass_check(*, artifact_type: str, task_schema: str) -> ValidationCheck:
    return ValidationCheck.from_dict(
        {
            "check_id": SCHEMA_VALIDATOR_ID,
            "status": ValidationStatus.PASS.value,
            "field": "artifact.content",
            "reason": None,
            "details": {
                "validator_contract_version": SCHEMA_VALIDATOR_CONTRACT_VERSION,
                "artifact_type": artifact_type,
                "task_output_schema": task_schema,
            },
        }
    )


def _fail_check(
    *,
    artifact_type: str,
    task_schema: str,
    field: str,
    reason: str,
) -> ValidationCheck:
    return ValidationCheck.from_dict(
        {
            "check_id": SCHEMA_VALIDATOR_ID,
            "status": ValidationStatus.FAIL.value,
            "field": field,
            "reason": reason,
            "details": {
                "validator_contract_version": SCHEMA_VALIDATOR_CONTRACT_VERSION,
                "artifact_type": artifact_type,
                "task_output_schema": task_schema,
            },
        }
    )


class DeterministicSchemaValidator:
    """V7.1 deterministic schema validator.

    The validator reads an already content-hash-verified artifact, applies one
    registered payload schema, and returns the frozen C1.7 ValidationResult.
    It deliberately does not persist that result or transition artifact/task
    lifecycle. Phase 9 review and later validation orchestration own those
    state changes.
    """

    def __init__(
        self,
        project_store: ProjectStore,
        schema_registry: ArtifactSchemaRegistry = DEFAULT_ARTIFACT_SCHEMA_REGISTRY,
    ) -> None:
        if not isinstance(project_store, ProjectStore):
            raise TypeError("project_store must be ProjectStore")
        if not isinstance(schema_registry, ArtifactSchemaRegistry):
            raise TypeError("schema_registry must be ArtifactSchemaRegistry")
        self._projects = project_store
        self._versions = ArtifactVersionStore(project_store)
        self._content = ArtifactContentStore(project_store)
        self._tasks = TaskStore(project_store)
        self._schemas = schema_registry

    @property
    def schema_registry(self) -> ArtifactSchemaRegistry:
        return self._schemas

    def validate(
        self,
        project_id: CanonicalId | str,
        artifact_id: CanonicalId | str,
        version: int,
    ) -> ValidationResult:
        try:
            record = self._versions.open_version(project_id, artifact_id, version)
        except (ArtifactVersionNotFoundError, ArtifactVersionConflictError) as exc:
            raise SchemaValidationConflictError(
                f"artifact could not be opened safely: {exc}"
            ) from exc

        try:
            task = self._tasks.open_task(record.project_id, record.producer_task)
        except (TaskNotFoundError, TaskStoreConflictError) as exc:
            raise SchemaValidationConflictError(
                "artifact producer task could not be opened safely"
            ) from exc

        if SCHEMA_VALIDATOR_ID not in task.validators:
            raise SchemaValidationConflictError(
                "producer task did not request the schema validator"
            )

        try:
            raw = self._content.read(record.project_id, record.hash)
        except ArtifactHashError as exc:
            raise SchemaValidationConflictError(
                "artifact content could not be read safely"
            ) from exc

        try:
            definition = self._schemas.get(record.type)
        except UnknownArtifactSchemaError as exc:
            return ValidationResult.from_checks(
                (
                    _fail_check(
                        artifact_type=record.type,
                        task_schema=task.output_schema,
                        field="artifact.type",
                        reason=str(exc),
                    ),
                )
            )

        try:
            payload_raw = _strict_json_object(raw)
            payload = definition.parser(payload_raw)
        except (ValueError, TypeError, ResultDraftArtifactConflictError) as exc:
            return ValidationResult.from_checks(
                (
                    _fail_check(
                        artifact_type=record.type,
                        task_schema=task.output_schema,
                        field="artifact.content",
                        reason=f"schema validation failed: {exc}",
                    ),
                )
            )
        except Exception as exc:  # fail closed around registered parser bugs
            raise SchemaValidationConflictError(
                "registered schema parser failed unexpectedly"
            ) from exc

        if isinstance(payload, ResultDraftArtifactPayload):
            if payload.project_id != record.project_id:
                return ValidationResult.from_checks(
                    (
                        _fail_check(
                            artifact_type=record.type,
                            task_schema=task.output_schema,
                            field="project_id",
                            reason="payload project_id does not match artifact project",
                        ),
                    )
                )
            if payload.task_id != record.producer_task:
                return ValidationResult.from_checks(
                    (
                        _fail_check(
                            artifact_type=record.type,
                            task_schema=task.output_schema,
                            field="task_id",
                            reason="payload task_id does not match artifact producer_task",
                        ),
                    )
                )
            if payload.role_id != task.role:
                return ValidationResult.from_checks(
                    (
                        _fail_check(
                            artifact_type=record.type,
                            task_schema=task.output_schema,
                            field="role_id",
                            reason="payload role_id does not match producer task role",
                        ),
                    )
                )
            if payload.schema_id != task.output_schema:
                return ValidationResult.from_checks(
                    (
                        _fail_check(
                            artifact_type=record.type,
                            task_schema=task.output_schema,
                            field="schema_id",
                            reason="payload schema_id does not match producer task output_schema",
                        ),
                    )
                )

        return ValidationResult.from_checks(
            (_pass_check(artifact_type=record.type, task_schema=task.output_schema),)
        )
