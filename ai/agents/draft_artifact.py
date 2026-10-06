from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import fcntl
import json
import os
import sqlite3
from typing import Any, Iterator, Mapping

from ai.agents.manifest import (
    ContextManifest,
    ContextManifestConflictError,
    ContextManifestNotFoundError,
    ContextManifestStore,
)
from ai.agents.runs import (
    AgentRunNotFoundError,
    AgentRunPersistenceConflictError,
    AgentRunRecord,
    AgentRunStore,
)
from ai.artifacts.hashing import (
    ArtifactContentStore,
    ArtifactHashError,
    compute_artifact_sha256,
)
from ai.artifacts.schema import ArtifactRecord, ArtifactSchemaError
from ai.artifacts.versions import (
    ARTIFACT_REGISTRY_DB_FILENAME,
    ArtifactVersionConflictError,
    ArtifactVersionInput,
    ArtifactVersionNotFoundError,
    ArtifactVersionStore,
    verify_artifact_registry,
)
from ai.contracts.agent_result import AgentResult, AgentResultError
from ai.contracts.artifact_states import ArtifactState
from ai.contracts.ids import CanonicalId, CanonicalIdError, IdKind
from ai.project.store import ProjectNotFoundError, ProjectStore, ProjectStoreConflictError
from ai.tasks.schema import TaskRecord
from ai.tasks.store import TaskNotFoundError, TaskStore, TaskStoreConflictError


RESULT_DRAFT_ARTIFACT_CONTRACT_VERSION = "1.0"
RESULT_DRAFT_ARTIFACT_TYPE = "ai.agent_result.v1"
MAX_RESULT_DRAFT_ARTIFACT_BYTES = 10 * 1024 * 1024
MAX_RESULT_ARTIFACT_RECOVERY_CANDIDATES = 4096
RESULT_DRAFT_ARTIFACT_LOCK_FILENAME = ".result-draft-artifact.lock"

_PAYLOAD_KEYS = frozenset(
    {
        "contract_version",
        "run_id",
        "project_id",
        "task_id",
        "role_id",
        "agent_identity",
        "schema_id",
        "context_manifest_sha256",
        "context_sha256",
        "result",
    }
)


class ResultDraftArtifactError(RuntimeError):
    """Base error for G6.8 result-to-DRAFT artifact creation."""


class ResultDraftArtifactConflictError(ResultDraftArtifactError):
    """Raised when run/context/artifact binding is unsafe or inconsistent."""


class ResultDraftArtifactNotFoundError(ResultDraftArtifactError):
    """Raised when a requested G6.8 result artifact cannot be reopened."""


def _sha256_text(raw: Any, *, field: str) -> str:
    if (
        not isinstance(raw, str)
        or len(raw) != 64
        or any(ch not in "0123456789abcdef" for ch in raw)
    ):
        raise ResultDraftArtifactConflictError(
            f"{field} must be 64 lowercase hexadecimal SHA-256 characters"
        )
    return raw


def _clean_text(raw: Any, *, field: str) -> str:
    if not isinstance(raw, str) or not raw or raw != raw.strip():
        raise ResultDraftArtifactConflictError(
            f"{field} must be clean non-empty text"
        )
    return raw


def _parse_id(raw: CanonicalId | str, *, kind: IdKind, field: str) -> CanonicalId:
    try:
        value = raw if isinstance(raw, CanonicalId) else CanonicalId.parse(raw)
    except (CanonicalIdError, TypeError) as exc:
        raise ResultDraftArtifactConflictError(
            f"{field} must be a canonical {kind.value} ID"
        ) from exc
    if value.kind is not kind:
        raise ResultDraftArtifactConflictError(
            f"{field} must identify {kind.value}"
        )
    return value


def _canonical_json_bytes(payload: Mapping[str, Any]) -> bytes:
    try:
        raw = (
            json.dumps(
                payload,
                ensure_ascii=False,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ResultDraftArtifactConflictError(
            "result artifact payload cannot be canonically encoded"
        ) from exc
    if len(raw) > MAX_RESULT_DRAFT_ARTIFACT_BYTES:
        raise ResultDraftArtifactConflictError(
            "result artifact payload exceeds MAX_RESULT_DRAFT_ARTIFACT_BYTES"
        )
    return raw


def _strict_json_object(raw: bytes) -> Mapping[str, Any]:
    if len(raw) > MAX_RESULT_DRAFT_ARTIFACT_BYTES:
        raise ResultDraftArtifactConflictError(
            "result artifact payload exceeds MAX_RESULT_DRAFT_ARTIFACT_BYTES"
        )

    def reject_constant(value: str) -> None:
        raise ResultDraftArtifactConflictError(
            f"result artifact contains non-finite JSON constant {value!r}"
        )

    def no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ResultDraftArtifactConflictError(
                    f"duplicate JSON key in result artifact: {key}"
                )
            result[key] = value
        return result

    try:
        payload = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=no_duplicates,
            parse_constant=reject_constant,
        )
    except UnicodeDecodeError as exc:
        raise ResultDraftArtifactConflictError(
            "result artifact payload is not UTF-8"
        ) from exc
    except json.JSONDecodeError as exc:
        raise ResultDraftArtifactConflictError(
            "result artifact payload is invalid JSON"
        ) from exc
    if not isinstance(payload, Mapping):
        raise ResultDraftArtifactConflictError(
            "result artifact payload top level must be an object"
        )
    return payload


@dataclass(frozen=True)
class ResultDraftArtifactPayload:
    """Canonical G6.8 content persisted inside one Phase-4 artifact blob."""

    run_id: CanonicalId
    project_id: CanonicalId
    task_id: CanonicalId
    role_id: str
    agent_identity: str
    schema_id: str
    context_manifest_sha256: str
    context_sha256: str
    result: AgentResult

    def __post_init__(self) -> None:
        run_id = _parse_id(self.run_id, kind=IdKind.AGENT_RUN, field="run_id")
        project_id = _parse_id(
            self.project_id, kind=IdKind.PROJECT, field="project_id"
        )
        task_id = _parse_id(self.task_id, kind=IdKind.TASK, field="task_id")
        role_id = _clean_text(self.role_id, field="role_id")
        agent_identity = _clean_text(
            self.agent_identity, field="agent_identity"
        )
        schema_id = _clean_text(self.schema_id, field="schema_id")
        if agent_identity != f"{role_id}:{task_id.value}":
            raise ResultDraftArtifactConflictError(
                "agent_identity does not match role/task identity"
            )
        manifest_sha = _sha256_text(
            self.context_manifest_sha256,
            field="context_manifest_sha256",
        )
        context_sha = _sha256_text(
            self.context_sha256,
            field="context_sha256",
        )
        if not isinstance(self.result, AgentResult):
            raise ResultDraftArtifactConflictError("result must be AgentResult")
        if self.result.to_dict().get("status") != "PROPOSED":
            raise ResultDraftArtifactConflictError(
                "G6.8 accepts only PROPOSED agent results"
            )

        object.__setattr__(self, "run_id", run_id)
        object.__setattr__(self, "project_id", project_id)
        object.__setattr__(self, "task_id", task_id)
        object.__setattr__(self, "role_id", role_id)
        object.__setattr__(self, "agent_identity", agent_identity)
        object.__setattr__(self, "schema_id", schema_id)
        object.__setattr__(self, "context_manifest_sha256", manifest_sha)
        object.__setattr__(self, "context_sha256", context_sha)

    @classmethod
    def from_run_and_manifest(
        cls,
        run: AgentRunRecord,
        manifest: ContextManifest,
    ) -> "ResultDraftArtifactPayload":
        if not isinstance(run, AgentRunRecord):
            raise TypeError("run must be AgentRunRecord")
        if not isinstance(manifest, ContextManifest):
            raise TypeError("manifest must be ContextManifest")
        if (
            manifest.run_id != run.run_id.value
            or manifest.project_id != run.project_id.value
            or manifest.task_id != run.task_id.value
            or manifest.role_id != run.role_id
            or manifest.agent_identity != run.agent_identity
            or manifest.schema_id != run.schema_id
        ):
            raise ResultDraftArtifactConflictError(
                "run/context manifest binding mismatch"
            )
        return cls(
            run_id=run.run_id,
            project_id=run.project_id,
            task_id=run.task_id,
            role_id=run.role_id,
            agent_identity=run.agent_identity,
            schema_id=run.schema_id,
            context_manifest_sha256=manifest.manifest_sha256,
            context_sha256=manifest.context_sha256,
            result=run.result,
        )

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "ResultDraftArtifactPayload":
        if not isinstance(raw, Mapping) or frozenset(raw.keys()) != _PAYLOAD_KEYS:
            raise ResultDraftArtifactConflictError(
                "result artifact payload must contain exactly the G6.8 fields"
            )
        if raw["contract_version"] != RESULT_DRAFT_ARTIFACT_CONTRACT_VERSION:
            raise ResultDraftArtifactConflictError(
                "result artifact contract_version mismatch"
            )
        try:
            result = AgentResult.from_dict(raw["result"])
        except (AgentResultError, TypeError) as exc:
            raise ResultDraftArtifactConflictError(
                "result artifact contains invalid AgentResult"
            ) from exc
        return cls(
            run_id=raw["run_id"],
            project_id=raw["project_id"],
            task_id=raw["task_id"],
            role_id=raw["role_id"],
            agent_identity=raw["agent_identity"],
            schema_id=raw["schema_id"],
            context_manifest_sha256=raw["context_manifest_sha256"],
            context_sha256=raw["context_sha256"],
            result=result,
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "contract_version": RESULT_DRAFT_ARTIFACT_CONTRACT_VERSION,
            "run_id": self.run_id.value,
            "project_id": self.project_id.value,
            "task_id": self.task_id.value,
            "role_id": self.role_id,
            "agent_identity": self.agent_identity,
            "schema_id": self.schema_id,
            "context_manifest_sha256": self.context_manifest_sha256,
            "context_sha256": self.context_sha256,
            "result": self.result.to_dict(),
        }

    def canonical_bytes(self) -> bytes:
        return _canonical_json_bytes(self.to_dict())


@dataclass(frozen=True)
class ResultDraftArtifact:
    """Verified pairing of one Phase-4 artifact version and its G6.8 payload."""

    record: ArtifactRecord
    payload: ResultDraftArtifactPayload

    def __post_init__(self) -> None:
        if not isinstance(self.record, ArtifactRecord):
            raise ResultDraftArtifactConflictError("record must be ArtifactRecord")
        if not isinstance(self.payload, ResultDraftArtifactPayload):
            raise ResultDraftArtifactConflictError(
                "payload must be ResultDraftArtifactPayload"
            )
        if self.record.type != RESULT_DRAFT_ARTIFACT_TYPE:
            raise ResultDraftArtifactConflictError(
                "artifact type is not the G6.8 result artifact type"
            )
        if self.record.project_id != self.payload.project_id:
            raise ResultDraftArtifactConflictError(
                "artifact/payload project mismatch"
            )
        if self.record.producer_task != self.payload.task_id:
            raise ResultDraftArtifactConflictError(
                "artifact producer task does not match run task"
            )
        if self.record.hash != compute_artifact_sha256(
            self.payload.canonical_bytes()
        ):
            raise ResultDraftArtifactConflictError(
                "artifact hash does not match canonical G6.8 payload"
            )


class ResultDraftArtifactStore:
    """G6.8 bridge from verified AI-RUN + context manifest to one DRAFT artifact.

    The store reuses the frozen Phase-4 ArtifactVersionStore for canonical ART
    allocation, content hashing, dependency edges and lifecycle creation. It
    does not review, validate, approve, supersede, integrate, call a provider,
    change task state, or command the UAV runtime.
    """

    def __init__(self, project_store: ProjectStore) -> None:
        if not isinstance(project_store, ProjectStore):
            raise TypeError("project_store must be ProjectStore")
        self._projects = project_store
        self._runs = AgentRunStore(project_store)
        self._manifests = ContextManifestStore(project_store)
        self._tasks = TaskStore(project_store)
        self._versions = ArtifactVersionStore(project_store)
        self._content = ArtifactContentStore(project_store)

    def _open_handle(self, project_id: CanonicalId | str):
        project = _parse_id(
            project_id, kind=IdKind.PROJECT, field="project_id"
        )
        try:
            return self._projects.open_project(project)
        except (ProjectNotFoundError, ProjectStoreConflictError) as exc:
            raise ResultDraftArtifactConflictError(
                "project could not be opened"
            ) from exc

    def _validated_inputs(
        self,
        project_id: CanonicalId | str,
        run_id: CanonicalId | str,
    ) -> tuple[AgentRunRecord, ContextManifest, TaskRecord]:
        try:
            run = self._runs.open_run(project_id, run_id)
            manifest = self._manifests.open_manifest(project_id, run.run_id)
            task = self._tasks.open_task(run.project_id, run.task_id)
        except (
            AgentRunNotFoundError,
            AgentRunPersistenceConflictError,
            ContextManifestNotFoundError,
            ContextManifestConflictError,
            TaskNotFoundError,
            TaskStoreConflictError,
        ) as exc:
            raise ResultDraftArtifactConflictError(
                "run/context/task inputs could not be reopened safely"
            ) from exc

        if task.project_id != run.project_id or task.task_id != run.task_id:
            raise ResultDraftArtifactConflictError("run/task identity mismatch")
        if task.role != run.role_id or task.output_schema != run.schema_id:
            raise ResultDraftArtifactConflictError(
                "run no longer matches immutable task contract"
            )
        return run, manifest, task

    @staticmethod
    def _parse_artifact_record_json(raw: str) -> ArtifactRecord:
        def reject_constant(value: str) -> None:
            raise ResultDraftArtifactConflictError(
                f"artifact registry contains non-finite JSON constant {value!r}"
            )

        def no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
            result: dict[str, Any] = {}
            for key, value in pairs:
                if key in result:
                    raise ResultDraftArtifactConflictError(
                        f"duplicate artifact registry JSON key: {key}"
                    )
                result[key] = value
            return result

        try:
            payload = json.loads(
                raw,
                object_pairs_hook=no_duplicates,
                parse_constant=reject_constant,
            )
            return ArtifactRecord.from_dict(payload)
        except (json.JSONDecodeError, ArtifactSchemaError, TypeError) as exc:
            raise ResultDraftArtifactConflictError(
                "artifact registry contains invalid immutable record"
            ) from exc

    @staticmethod
    @contextmanager
    def _creation_lock(lock_path) -> Iterator[None]:
        if lock_path.is_symlink():
            raise ResultDraftArtifactConflictError(
                "result artifact lock path must not be a symlink"
            )
        if lock_path.exists() and not lock_path.is_file():
            raise ResultDraftArtifactConflictError(
                "result artifact lock path must be a regular file"
            )
        try:
            fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
        except OSError as exc:
            raise ResultDraftArtifactConflictError(
                "result artifact coordination lock could not be opened"
            ) from exc
        try:
            os.chmod(lock_path, 0o600)
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        except OSError as exc:
            raise ResultDraftArtifactConflictError(
                "result artifact coordination lock failed"
            ) from exc
        finally:
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            finally:
                os.close(fd)

    def _find_existing(
        self,
        project_id: CanonicalId,
        run: AgentRunRecord,
        expected_hash: str,
    ) -> ResultDraftArtifact | None:
        handle = self._open_handle(project_id)
        registry = handle.layout.project_dir / ARTIFACT_REGISTRY_DB_FILENAME
        if not registry.exists():
            return None
        if registry.is_symlink() or not registry.is_file():
            raise ResultDraftArtifactConflictError(
                "artifact registry path is unsafe"
            )

        try:
            connection = sqlite3.connect(registry)
            try:
                verify_artifact_registry(connection)
                rows = connection.execute(
                    """
                    SELECT artifact_id, version, record_json
                    FROM artifact_versions
                    WHERE artifact_type=?
                    ORDER BY artifact_id, version
                    LIMIT ?
                    """,
                    (
                        RESULT_DRAFT_ARTIFACT_TYPE,
                        MAX_RESULT_ARTIFACT_RECOVERY_CANDIDATES + 1,
                    ),
                ).fetchall()
            finally:
                connection.close()
        except sqlite3.DatabaseError as exc:
            raise ResultDraftArtifactConflictError(
                "artifact registry recovery query failed"
            ) from exc

        if len(rows) > MAX_RESULT_ARTIFACT_RECOVERY_CANDIDATES:
            raise ResultDraftArtifactConflictError(
                "result artifact recovery exceeds bounded candidate limit"
            )

        for artifact_id, version, record_json in rows:
            record = self._parse_artifact_record_json(record_json)
            if (
                record.hash == expected_hash
                and record.project_id == project_id
                and record.producer_task == run.task_id
            ):
                reopened = self.open_artifact(project_id, artifact_id, version)
                if reopened.payload.run_id != run.run_id:
                    raise ResultDraftArtifactConflictError(
                        "result artifact content hash is bound to a different run"
                    )
                if reopened.record.status is not ArtifactState.DRAFT:
                    raise ResultDraftArtifactConflictError(
                        "existing result artifact is no longer DRAFT"
                    )
                return reopened
        return None

    def create_artifact(
        self,
        project_id: CanonicalId | str,
        run_id: CanonicalId | str,
        *,
        created_timestamp: str,
    ) -> ResultDraftArtifact:
        run, manifest, task = self._validated_inputs(project_id, run_id)
        payload = ResultDraftArtifactPayload.from_run_and_manifest(run, manifest)
        content = payload.canonical_bytes()
        expected_hash = compute_artifact_sha256(content)
        handle = self._open_handle(run.project_id)
        lock_path = (
            handle.layout.data_directories["ai_runs"]
            / RESULT_DRAFT_ARTIFACT_LOCK_FILENAME
        )
        with self._creation_lock(lock_path):
            existing = self._find_existing(
                run.project_id, run, expected_hash
            )
            if existing is not None:
                return existing

            try:
                record = self._versions.create_artifact(
                    run.project_id,
                    ArtifactVersionInput(
                        type=RESULT_DRAFT_ARTIFACT_TYPE,
                        producer_task=run.task_id,
                        input_dependency_versions=task.input_artifacts,
                        content=content,
                        created_timestamp=created_timestamp,
                    ),
                )
            except (ArtifactVersionConflictError, ArtifactHashError) as exc:
                raise ResultDraftArtifactConflictError(
                    "Phase-4 artifact creation failed"
                ) from exc

        if record.status is not ArtifactState.DRAFT:
            raise ResultDraftArtifactConflictError(
                "new G6.8 artifact did not enter DRAFT"
            )
        if record.validation_result is not None:
            raise ResultDraftArtifactConflictError(
                "new G6.8 artifact must not contain a validation result"
            )
        return self.open_artifact(
            run.project_id, record.artifact_id, record.version
        )

    def open_artifact(
        self,
        project_id: CanonicalId | str,
        artifact_id: CanonicalId | str,
        version: int = 1,
    ) -> ResultDraftArtifact:
        try:
            record = self._versions.open_version(
                project_id, artifact_id, version
            )
        except ArtifactVersionNotFoundError as exc:
            raise ResultDraftArtifactNotFoundError(
                "result artifact could not be opened"
            ) from exc
        except ArtifactVersionConflictError as exc:
            raise ResultDraftArtifactConflictError(
                "result artifact registry/content integrity failure"
            ) from exc
        if record.type != RESULT_DRAFT_ARTIFACT_TYPE:
            raise ResultDraftArtifactConflictError(
                "artifact is not a G6.8 result artifact"
            )
        try:
            raw = self._content.read(record.project_id, record.hash)
        except ArtifactHashError as exc:
            raise ResultDraftArtifactConflictError(
                "result artifact content could not be read safely"
            ) from exc
        payload = ResultDraftArtifactPayload.from_dict(_strict_json_object(raw))

        try:
            run = self._runs.open_run(record.project_id, payload.run_id)
            manifest = self._manifests.open_manifest(
                record.project_id, payload.run_id
            )
            task = self._tasks.open_task(record.project_id, payload.task_id)
        except (
            AgentRunNotFoundError,
            AgentRunPersistenceConflictError,
            ContextManifestNotFoundError,
            ContextManifestConflictError,
            TaskNotFoundError,
            TaskStoreConflictError,
        ) as exc:
            raise ResultDraftArtifactConflictError(
                "result artifact references invalid persisted inputs"
            ) from exc

        expected = ResultDraftArtifactPayload.from_run_and_manifest(run, manifest)
        if payload != expected:
            raise ResultDraftArtifactConflictError(
                "result artifact payload no longer matches run/context manifest"
            )
        if record.producer_task != task.task_id:
            raise ResultDraftArtifactConflictError(
                "result artifact producer_task mismatch"
            )
        if record.input_dependency_versions != task.input_artifacts:
            raise ResultDraftArtifactConflictError(
                "result artifact dependency lineage does not match task inputs"
            )
        return ResultDraftArtifact(record=record, payload=payload)
