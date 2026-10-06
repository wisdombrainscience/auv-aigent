from __future__ import annotations

from dataclasses import dataclass
import json
import math
import re
from types import MappingProxyType
from typing import Any, Mapping, TypeAlias

from ai.agents.mapping import (
    TaskAgentBinding,
    TaskAgentMapper,
    TaskAgentMappingError,
)
from ai.contracts.agent_result import AgentResult
from ai.contracts.provenance import ProvenanceClass
from ai.project.store import ProjectStore
from ai.providers.contracts import GenerateResponse
from ai.providers.model_output import parse_agent_result_response
from ai.tasks.schema import TaskRecord
from ai.tasks.store import TaskNotFoundError, TaskStore, TaskStoreConflictError


SCHEMA_ENFORCED_OUTPUT_CONTRACT_VERSION = "1.0"
AGENT_RESULT_OUTPUT_SCHEMA_ID = "engineering_task.v1"
MAX_OUTPUT_SCHEMA_ID_CHARS = 128
MAX_OUTPUT_SCHEMA_BYTES = 64 * 1024

_SCHEMA_ID_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,127}$")

JSONScalar: TypeAlias = None | bool | int | float | str
JSONValue: TypeAlias = (
    JSONScalar
    | tuple["JSONValue", ...]
    | Mapping[str, "JSONValue"]
)


class SchemaEnforcedOutputError(RuntimeError):
    """Base error for deterministic G6.5 task-output schema enforcement."""


class OutputSchemaConflictError(SchemaEnforcedOutputError):
    """Raised when schema configuration or task binding is internally unsafe."""


class UnknownOutputSchemaError(SchemaEnforcedOutputError):
    """Raised when a task selects an output schema not enabled by G6.5."""


def _clean_schema_id(raw: str) -> str:
    if not isinstance(raw, str):
        raise OutputSchemaConflictError("schema_id must be a string")
    if len(raw) > MAX_OUTPUT_SCHEMA_ID_CHARS:
        raise OutputSchemaConflictError(
            "schema_id exceeds MAX_OUTPUT_SCHEMA_ID_CHARS"
        )
    if _SCHEMA_ID_RE.fullmatch(raw) is None:
        raise OutputSchemaConflictError(
            "schema_id must match [A-Za-z][A-Za-z0-9_.-]{0,127}"
        )
    return raw


def _freeze_json(value: Any, *, path: str) -> JSONValue:
    if value is None or isinstance(value, (bool, str)):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise OutputSchemaConflictError(
                f"{path} contains a non-finite number"
            )
        return value
    if isinstance(value, (list, tuple)):
        return tuple(
            _freeze_json(item, path=f"{path}[{index}]")
            for index, item in enumerate(value)
        )
    if isinstance(value, Mapping):
        frozen: dict[str, JSONValue] = {}
        for key, item in value.items():
            if not isinstance(key, str) or not key:
                raise OutputSchemaConflictError(
                    f"{path} contains an invalid object key"
                )
            frozen[key] = _freeze_json(item, path=f"{path}.{key}")
        return MappingProxyType(frozen)
    raise OutputSchemaConflictError(
        f"{path} contains unsupported JSON value type {type(value).__name__}"
    )


def _thaw_json(value: JSONValue) -> Any:
    if isinstance(value, tuple):
        return [_thaw_json(item) for item in value]
    if isinstance(value, Mapping):
        return {key: _thaw_json(item) for key, item in value.items()}
    return value


def _canonical_json_size(value: Mapping[str, JSONValue]) -> int:
    try:
        payload = json.dumps(
            _thaw_json(value),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise OutputSchemaConflictError(
            "output schema cannot be canonically encoded"
        ) from exc
    return len(payload)


def _agent_result_json_schema() -> dict[str, Any]:
    """Return the strict provider-facing schema for C1.5 AgentResult.

    The existing `AgentResult` parser remains authoritative after generation.
    This JSON schema constrains local-provider structured generation and is not
    a replacement for deterministic post-generation validation.
    """

    provenance = [item.value for item in ProvenanceClass]
    return {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "status",
            "claims",
            "assumptions",
            "calculations",
            "artifacts",
            "risks",
            "missing_information",
            "open_questions",
        ],
        "properties": {
            "status": {
                "type": "string",
                "enum": ["PROPOSED"],
            },
            "claims": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["classification"],
                    "properties": {
                        "classification": {
                            "type": "string",
                            "enum": provenance,
                        },
                    },
                    "additionalProperties": True,
                },
            },
            "assumptions": {"type": "array"},
            "calculations": {"type": "array"},
            "artifacts": {"type": "array"},
            "risks": {"type": "array"},
            "missing_information": {
                "type": "array",
                "items": {"type": "string", "minLength": 1},
            },
            "open_questions": {
                "type": "array",
                "items": {"type": "string", "minLength": 1},
            },
        },
    }


@dataclass(frozen=True)
class OutputSchemaDefinition:
    """Immutable named output-schema snapshot enabled for one task contract."""

    schema_id: str
    contract_name: str
    json_schema: Mapping[str, JSONValue]

    def __post_init__(self) -> None:
        object.__setattr__(self, "schema_id", _clean_schema_id(self.schema_id))
        if not isinstance(self.contract_name, str) or not self.contract_name:
            raise OutputSchemaConflictError(
                "contract_name must be non-empty text"
            )
        if self.contract_name != self.contract_name.strip():
            raise OutputSchemaConflictError(
                "contract_name must not contain surrounding whitespace"
            )
        if not isinstance(self.json_schema, Mapping):
            raise OutputSchemaConflictError("json_schema must be an object")
        frozen = _freeze_json(self.json_schema, path="json_schema")
        assert isinstance(frozen, Mapping)
        if _canonical_json_size(frozen) > MAX_OUTPUT_SCHEMA_BYTES:
            raise OutputSchemaConflictError(
                "json_schema exceeds MAX_OUTPUT_SCHEMA_BYTES"
            )
        object.__setattr__(self, "json_schema", frozen)

    def schema_dict(self) -> dict[str, Any]:
        return _thaw_json(self.json_schema)


class OutputSchemaRegistry:
    """Deterministic bounded lookup for G6.5 task output schemas.

    G6.5 intentionally activates only the generic engineering AgentResult
    envelope. Domain validators/schemas remain later deterministic-trust work.
    """

    def __init__(self, definitions: tuple[OutputSchemaDefinition, ...]) -> None:
        if not isinstance(definitions, tuple):
            raise TypeError("definitions must be a tuple")
        if not definitions:
            raise OutputSchemaConflictError(
                "at least one output schema must be registered"
            )
        by_id: dict[str, OutputSchemaDefinition] = {}
        for definition in definitions:
            if not isinstance(definition, OutputSchemaDefinition):
                raise OutputSchemaConflictError(
                    "registry entries must be OutputSchemaDefinition"
                )
            if definition.schema_id in by_id:
                raise OutputSchemaConflictError(
                    f"duplicate output schema: {definition.schema_id}"
                )
            by_id[definition.schema_id] = definition
        self._definitions = MappingProxyType(by_id)

    def schema_ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._definitions))

    def contains(self, schema_id: str) -> bool:
        try:
            canonical = _clean_schema_id(schema_id)
        except OutputSchemaConflictError:
            return False
        return canonical in self._definitions

    def get(self, schema_id: str) -> OutputSchemaDefinition:
        canonical = _clean_schema_id(schema_id)
        try:
            return self._definitions[canonical]
        except KeyError as exc:
            raise UnknownOutputSchemaError(
                f"output schema is not enabled: {canonical!r}"
            ) from exc


DEFAULT_OUTPUT_SCHEMA_REGISTRY = OutputSchemaRegistry(
    (
        OutputSchemaDefinition(
            schema_id=AGENT_RESULT_OUTPUT_SCHEMA_ID,
            contract_name="AgentResult",
            json_schema=_agent_result_json_schema(),
        ),
    )
)


@dataclass(frozen=True)
class SchemaEnforcedAgentOutput:
    """Validated G6.5 output bound to its persisted task and named schema."""

    binding: TaskAgentBinding
    schema_id: str
    result: AgentResult

    def __post_init__(self) -> None:
        if not isinstance(self.binding, TaskAgentBinding):
            raise OutputSchemaConflictError(
                "binding must be TaskAgentBinding"
            )
        object.__setattr__(self, "schema_id", _clean_schema_id(self.schema_id))
        if not isinstance(self.result, AgentResult):
            raise OutputSchemaConflictError("result must be AgentResult")

    def to_dict(self) -> dict[str, object]:
        return {
            "contract_version": SCHEMA_ENFORCED_OUTPUT_CONTRACT_VERSION,
            "binding": {
                "project_id": self.binding.project_id.value,
                "task_id": self.binding.task_id.value,
                "role_id": self.binding.role_id,
                "agent_identity": self.binding.agent.logical_identity,
            },
            "schema_id": self.schema_id,
            "result": self.result.to_dict(),
        }


class SchemaEnforcedOutputValidator:
    """Validate raw local-model responses against the persisted task schema.

    This component performs no provider call, retry, lifecycle transition, run
    persistence, context-manifest persistence, artifact creation, approval, or
    UAV action. It only binds task->agent, resolves the named output schema,
    and applies the existing strict AgentResult model-output parser.
    """

    def __init__(
        self,
        project_store: ProjectStore,
        schema_registry: OutputSchemaRegistry = DEFAULT_OUTPUT_SCHEMA_REGISTRY,
    ) -> None:
        if not isinstance(project_store, ProjectStore):
            raise TypeError("project_store must be ProjectStore")
        if not isinstance(schema_registry, OutputSchemaRegistry):
            raise TypeError("schema_registry must be OutputSchemaRegistry")
        self._projects = project_store
        self._tasks = TaskStore(project_store)
        self._mapper = TaskAgentMapper(project_store)
        self._schemas = schema_registry

    @property
    def schema_registry(self) -> OutputSchemaRegistry:
        return self._schemas

    def _task_and_binding(self, project_id, task_id) -> tuple[TaskRecord, TaskAgentBinding]:
        try:
            binding = self._mapper.map_task(project_id, task_id)
        except TaskAgentMappingError as exc:
            raise OutputSchemaConflictError(
                f"task-to-agent mapping failed: {exc}"
            ) from exc
        try:
            task = self._tasks.open_task(binding.project_id, binding.task_id)
        except (TaskNotFoundError, TaskStoreConflictError) as exc:
            raise OutputSchemaConflictError(
                "persisted task cannot be opened safely"
            ) from exc
        if task.role != binding.role_id:
            raise OutputSchemaConflictError(
                "task role changed during schema enforcement"
            )
        return task, binding

    def schema_for_task(self, project_id, task_id) -> OutputSchemaDefinition:
        task, _binding = self._task_and_binding(project_id, task_id)
        return self._schemas.get(task.output_schema)

    def provider_schema_for_task(self, project_id, task_id) -> dict[str, Any]:
        """Return a detached strict schema suitable for GenerateRequest."""

        return self.schema_for_task(project_id, task_id).schema_dict()

    def enforce(
        self,
        project_id,
        task_id,
        response: GenerateResponse,
    ) -> SchemaEnforcedAgentOutput:
        if not isinstance(response, GenerateResponse):
            raise TypeError("response must be GenerateResponse")

        task, binding = self._task_and_binding(project_id, task_id)
        definition = self._schemas.get(task.output_schema)

        if definition.contract_name != "AgentResult":
            raise OutputSchemaConflictError(
                "G6.5 only supports the AgentResult contract"
            )

        # Reuse the Phase-2 strict parser so duplicate keys, non-standard JSON,
        # non-finite values, the exact C1.5 envelope and provenance classes all
        # retain their existing MODEL_INVALID_OUTPUT failure semantics.
        result = parse_agent_result_response(response)

        return SchemaEnforcedAgentOutput(
            binding=binding,
            schema_id=definition.schema_id,
            result=result,
        )
