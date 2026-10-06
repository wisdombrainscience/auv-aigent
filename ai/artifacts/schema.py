from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import re
from typing import Any, Mapping

from ai.contracts.artifact_states import (
    ArtifactState,
    ArtifactStateError,
    parse_artifact_state,
)
from ai.contracts.ids import (
    CanonicalId,
    CanonicalIdError,
    IdKind,
)
from ai.contracts.validation_result import (
    ValidationResult,
    ValidationResultError,
)


ARTIFACT_SCHEMA_CONTRACT_VERSION = "1.0"

MAX_ARTIFACT_VERSION = 999_999
MAX_ARTIFACT_TYPE_CHARS = 128
MAX_ARTIFACT_INPUT_DEPENDENCIES = 64

_ARTIFACT_TYPE_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,127}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_UTC_TIMESTAMP_RE = re.compile(
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T"
    r"[0-9]{2}:[0-9]{2}:[0-9]{2}"
    r"(?:\.[0-9]{1,6})?Z$"
)

_ARTIFACT_INPUT_VERSION_KEYS = frozenset(
    {
        "entity_id",
        "version",
    }
)

_ARTIFACT_RECORD_KEYS = frozenset(
    {
        "artifact_id",
        "project_id",
        "version",
        "type",
        "producer_task",
        "input_dependency_versions",
        "hash",
        "status",
        "validation_result",
        "created_timestamp",
    }
)


class ArtifactSchemaError(ValueError):
    """Raised when an A4.1 artifact record violates the frozen schema."""


def _parse_id(
    raw: CanonicalId | str,
    *,
    kind: IdKind,
    field: str,
) -> CanonicalId:
    try:
        value = raw if isinstance(raw, CanonicalId) else CanonicalId.parse(raw)
    except (CanonicalIdError, TypeError) as exc:
        raise ArtifactSchemaError(
            f"{field} must be a valid canonical {kind.value} ID"
        ) from exc

    if value.kind is not kind:
        raise ArtifactSchemaError(
            f"{field} must be a canonical {kind.value} ID"
        )
    return value


def _parse_input_entity_id(
    raw: CanonicalId | str,
) -> CanonicalId:
    try:
        value = raw if isinstance(raw, CanonicalId) else CanonicalId.parse(raw)
    except (CanonicalIdError, TypeError) as exc:
        raise ArtifactSchemaError(
            "dependency entity_id must be a canonical requirement or artifact ID"
        ) from exc

    if value.kind not in {
        IdKind.REQUIREMENT,
        IdKind.ARTIFACT,
    }:
        raise ArtifactSchemaError(
            "dependency entity_id must identify a requirement or artifact"
        )
    return value


def _require_version(raw: Any, *, field: str) -> int:
    if isinstance(raw, bool) or not isinstance(raw, int):
        raise ArtifactSchemaError(f"{field} must be an integer")
    if not 1 <= raw <= MAX_ARTIFACT_VERSION:
        raise ArtifactSchemaError(
            f"{field} must be in 1..{MAX_ARTIFACT_VERSION}"
        )
    return raw


def _require_artifact_type(raw: Any) -> str:
    if not isinstance(raw, str):
        raise ArtifactSchemaError("type must be a string")
    if not raw or raw != raw.strip():
        raise ArtifactSchemaError(
            "type must be clean non-empty text"
        )
    if (
        len(raw) > MAX_ARTIFACT_TYPE_CHARS
        or _ARTIFACT_TYPE_RE.fullmatch(raw) is None
    ):
        raise ArtifactSchemaError(
            "type must match [A-Za-z][A-Za-z0-9_.-]{0,127}"
        )
    return raw


def _require_sha256(raw: Any) -> str:
    if not isinstance(raw, str) or _SHA256_RE.fullmatch(raw) is None:
        raise ArtifactSchemaError(
            "hash must be exactly 64 lowercase hexadecimal SHA-256 characters"
        )
    return raw


def _require_utc_timestamp(raw: Any) -> str:
    if not isinstance(raw, str):
        raise ArtifactSchemaError(
            "created_timestamp must be a string"
        )
    if _UTC_TIMESTAMP_RE.fullmatch(raw) is None:
        raise ArtifactSchemaError(
            "created_timestamp must be canonical UTC RFC3339 text ending in Z"
        )

    try:
        parsed = datetime.fromisoformat(
            raw[:-1] + "+00:00"
        )
    except ValueError as exc:
        raise ArtifactSchemaError(
            "created_timestamp is not a valid calendar timestamp"
        ) from exc

    if parsed.tzinfo != timezone.utc:
        raise ArtifactSchemaError(
            "created_timestamp must be UTC"
        )
    return raw


@dataclass(frozen=True, order=True)
class ArtifactInputVersion:
    """Schema-level version reference used by one artifact.

    A4.1 only captures the dependency version declared by the record.
    A4.3 owns persisted dependency edges, existence checks, graph semantics,
    cycle handling, and lineage traversal.
    """

    entity_id: CanonicalId
    version: int

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "entity_id",
            _parse_input_entity_id(self.entity_id),
        )
        object.__setattr__(
            self,
            "version",
            _require_version(
                self.version,
                field="dependency version",
            ),
        )

    @classmethod
    def from_dict(
        cls,
        raw: Mapping[str, Any],
    ) -> "ArtifactInputVersion":
        if not isinstance(raw, Mapping):
            raise ArtifactSchemaError(
                "input dependency version must be an object"
            )
        if frozenset(raw.keys()) != _ARTIFACT_INPUT_VERSION_KEYS:
            raise ArtifactSchemaError(
                "input dependency version must contain exactly "
                "entity_id,version"
            )
        return cls(
            entity_id=raw["entity_id"],
            version=raw["version"],
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "entity_id": self.entity_id.value,
            "version": self.version,
        }


@dataclass(frozen=True)
class ArtifactRecord:
    """Immutable A4.1 schema for one artifact version.

    This contract intentionally does not allocate IDs, persist versions,
    compute hashes, execute state transitions, or build a dependency graph.
    Those responsibilities remain gated to A4.2-A4.5.

    `validation_result` is a required schema field but may be null before a
    deterministic validator has produced a result.
    """

    artifact_id: CanonicalId
    project_id: CanonicalId
    version: int
    type: str
    producer_task: CanonicalId
    input_dependency_versions: tuple[ArtifactInputVersion, ...]
    hash: str
    status: ArtifactState
    validation_result: ValidationResult | None
    created_timestamp: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "artifact_id",
            _parse_id(
                self.artifact_id,
                kind=IdKind.ARTIFACT,
                field="artifact_id",
            ),
        )
        object.__setattr__(
            self,
            "project_id",
            _parse_id(
                self.project_id,
                kind=IdKind.PROJECT,
                field="project_id",
            ),
        )
        object.__setattr__(
            self,
            "version",
            _require_version(
                self.version,
                field="version",
            ),
        )
        object.__setattr__(
            self,
            "type",
            _require_artifact_type(self.type),
        )
        object.__setattr__(
            self,
            "producer_task",
            _parse_id(
                self.producer_task,
                kind=IdKind.TASK,
                field="producer_task",
            ),
        )

        raw_dependencies = self.input_dependency_versions
        if not isinstance(raw_dependencies, tuple):
            try:
                raw_dependencies = tuple(raw_dependencies)
            except TypeError as exc:
                raise ArtifactSchemaError(
                    "input_dependency_versions must be iterable"
                ) from exc

        if len(raw_dependencies) > MAX_ARTIFACT_INPUT_DEPENDENCIES:
            raise ArtifactSchemaError(
                "input_dependency_versions exceeds "
                "MAX_ARTIFACT_INPUT_DEPENDENCIES"
            )

        dependencies: list[ArtifactInputVersion] = []
        seen: set[tuple[str, int]] = set()
        for index, dependency in enumerate(raw_dependencies):
            if not isinstance(dependency, ArtifactInputVersion):
                raise ArtifactSchemaError(
                    f"input_dependency_versions[{index}] "
                    "must be ArtifactInputVersion"
                )
            key = (
                dependency.entity_id.value,
                dependency.version,
            )
            if key in seen:
                raise ArtifactSchemaError(
                    "duplicate input dependency version: "
                    f"{dependency.entity_id.value}@v{dependency.version}"
                )
            seen.add(key)
            dependencies.append(dependency)

        object.__setattr__(
            self,
            "input_dependency_versions",
            tuple(dependencies),
        )
        object.__setattr__(
            self,
            "hash",
            _require_sha256(self.hash),
        )

        try:
            status = parse_artifact_state(self.status)
        except (ArtifactStateError, TypeError) as exc:
            raise ArtifactSchemaError(
                "status is not a valid ArtifactState"
            ) from exc
        object.__setattr__(self, "status", status)

        validation = self.validation_result
        if validation is not None and not isinstance(
            validation,
            ValidationResult,
        ):
            raise ArtifactSchemaError(
                "validation_result must be ValidationResult or null"
            )

        object.__setattr__(
            self,
            "created_timestamp",
            _require_utc_timestamp(self.created_timestamp),
        )

    @classmethod
    def from_dict(
        cls,
        raw: Mapping[str, Any],
    ) -> "ArtifactRecord":
        if not isinstance(raw, Mapping):
            raise ArtifactSchemaError(
                "artifact record payload must be an object"
            )
        if frozenset(raw.keys()) != _ARTIFACT_RECORD_KEYS:
            missing = sorted(
                _ARTIFACT_RECORD_KEYS - frozenset(raw.keys())
            )
            extra = sorted(
                frozenset(raw.keys()) - _ARTIFACT_RECORD_KEYS
            )
            detail: list[str] = []
            if missing:
                detail.append(
                    "missing=" + ",".join(missing)
                )
            if extra:
                detail.append(
                    "extra=" + ",".join(
                        str(item)
                        for item in extra
                    )
                )
            raise ArtifactSchemaError(
                "artifact record keys do not match A4.1 contract"
                + (
                    " (" + "; ".join(detail) + ")"
                    if detail
                    else ""
                )
            )

        raw_dependencies = raw["input_dependency_versions"]
        if not isinstance(raw_dependencies, (list, tuple)):
            raise ArtifactSchemaError(
                "input_dependency_versions must be an array"
            )

        raw_validation = raw["validation_result"]
        if raw_validation is None:
            validation = None
        else:
            if not isinstance(raw_validation, Mapping):
                raise ArtifactSchemaError(
                    "validation_result must be an object or null"
                )
            try:
                validation = ValidationResult.from_dict(
                    raw_validation
                )
            except ValidationResultError as exc:
                raise ArtifactSchemaError(
                    f"validation_result is invalid: {exc}"
                ) from exc

        return cls(
            artifact_id=raw["artifact_id"],
            project_id=raw["project_id"],
            version=raw["version"],
            type=raw["type"],
            producer_task=raw["producer_task"],
            input_dependency_versions=tuple(
                ArtifactInputVersion.from_dict(item)
                for item in raw_dependencies
            ),
            hash=raw["hash"],
            status=raw["status"],
            validation_result=validation,
            created_timestamp=raw["created_timestamp"],
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "artifact_id": self.artifact_id.value,
            "project_id": self.project_id.value,
            "version": self.version,
            "type": self.type,
            "producer_task": self.producer_task.value,
            "input_dependency_versions": [
                item.to_dict()
                for item in self.input_dependency_versions
            ],
            "hash": self.hash,
            "status": self.status.value,
            "validation_result": (
                None
                if self.validation_result is None
                else self.validation_result.to_dict()
            ),
            "created_timestamp": self.created_timestamp,
        }


def parse_artifact_record(
    raw: Mapping[str, Any],
) -> ArtifactRecord:
    return ArtifactRecord.from_dict(raw)
