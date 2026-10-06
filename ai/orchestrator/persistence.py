from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import tempfile
from typing import Any, Mapping

from ai.contracts.ids import CanonicalId, CanonicalIdError, IdKind
from ai.contracts.task_states import TaskState
from ai.contracts.validation_result import ValidationStatus
from ai.orchestrator.proposal import ProposedTask, TaskPlanProposal
from ai.orchestrator.validator import DEFAULT_PLAN_VALIDATOR, PLAN_VALIDATOR_CHECK_IDS, PlanValidator
from ai.project.requirement_versions import (
    RequirementVersionConflictError,
    RequirementVersionNotFoundError,
    RequirementVersionStore,
)
from ai.project.requirements import RequirementSetStatus
from ai.project.schema import ProjectSchemaError, verify_project_schema
from ai.project.store import ProjectStore
from ai.tasks.dag import TaskDAGConflictError, TaskDAGCycleError, TaskDAGValidator
from ai.tasks.schema import TaskRecord, TaskRecordError
from ai.tasks.store import (
    TaskStore,
    TaskStoreConflictError,
    _attach_registry,
    _canonical_record_json,
    _insert_dependency_edges,
    _registry_path,
    _verify_attached_registry,
    initialize_task_registry,
)


APPROVED_DAG_PERSISTENCE_CONTRACT_VERSION = "1.0"
APPROVED_DAG_DIRECTORY_NAME = "approved_dags"
APPROVED_DAG_JOURNAL_FILENAME = ".approved-dag-transaction.json"
APPROVED_DAG_RECEIPT_FORMAT_VERSION = 1
APPROVED_DAG_JOURNAL_FORMAT_VERSION = 1
MAX_APPROVED_DAG_METADATA_BYTES = 1024 * 1024

_RECEIPT_KEYS = frozenset({
    "format_version",
    "project_id",
    "requirement_version",
    "proposal_sha256",
    "task_bindings",
})
_BINDING_KEYS = frozenset({"candidate_task_id", "persisted_task_id"})
_JOURNAL_KEYS = frozenset({
    "format_version",
    "project_id",
    "requirement_version",
    "proposal_sha256",
    "previous_task_sequence",
    "task_bindings",
    "records",
})


class ApprovedDAGPersistenceError(RuntimeError):
    """Base error for O8.8 approved-DAG persistence."""


class ApprovedDAGValidationError(ApprovedDAGPersistenceError):
    """Raised when a proposal is not eligible for persistence."""


class ApprovedDAGConflictError(ApprovedDAGPersistenceError):
    """Raised when persistent state conflicts with an approved proposal."""


def _parse_id(raw: CanonicalId | str, *, kind: IdKind, field: str) -> CanonicalId:
    try:
        value = raw if isinstance(raw, CanonicalId) else CanonicalId.parse(raw)
    except (CanonicalIdError, TypeError) as exc:
        raise ApprovedDAGConflictError(f"{field} must be canonical") from exc
    if value.kind is not kind:
        raise ApprovedDAGConflictError(f"{field} must identify {kind.value}")
    return value


def _canonical_json(payload: Mapping[str, object], *, label: str) -> bytes:
    try:
        raw = (
            json.dumps(
                dict(payload),
                ensure_ascii=False,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ApprovedDAGConflictError(f"{label} could not be serialized") from exc
    if len(raw) > MAX_APPROVED_DAG_METADATA_BYTES:
        raise ApprovedDAGConflictError(f"{label} exceeds bounded metadata size")
    return raw


def _read_json(path: Path, *, label: str) -> Mapping[str, Any]:
    if path.is_symlink():
        raise ApprovedDAGConflictError(f"{label} must not be a symbolic link")
    if not path.is_file():
        raise ApprovedDAGConflictError(f"{label} is not a regular file")
    raw = path.read_bytes()
    if not raw or len(raw) > MAX_APPROVED_DAG_METADATA_BYTES:
        raise ApprovedDAGConflictError(f"{label} has invalid size")

    def reject_constant(value: str) -> None:
        raise ApprovedDAGConflictError(f"{label} contains non-finite JSON constant: {value}")

    def no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for key, value in pairs:
            if key in out:
                raise ApprovedDAGConflictError(f"{label} contains duplicate JSON key: {key}")
            out[key] = value
        return out

    try:
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=no_duplicates,
            parse_constant=reject_constant,
        )
    except ApprovedDAGConflictError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ApprovedDAGConflictError(f"{label} is invalid JSON") from exc
    if not isinstance(value, Mapping):
        raise ApprovedDAGConflictError(f"{label} root must be an object")
    return value


def _atomic_write_new(path: Path, raw: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.parent.is_symlink():
        raise ApprovedDAGConflictError("approved DAG directory must not be a symbolic link")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    try:
        fd = os.open(path, flags, 0o600)
    except FileExistsError as exc:
        raise ApprovedDAGConflictError(f"persistent metadata already exists: {path.name}") from exc
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
    except Exception:
        path.unlink(missing_ok=True)
        raise


def _atomic_replace(path: Path, raw: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.parent.is_symlink():
        raise ApprovedDAGConflictError("approved DAG directory must not be a symbolic link")
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    tmp = Path(name)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def _proposal_sha256(proposal: TaskPlanProposal) -> str:
    return hashlib.sha256(proposal.to_json_bytes()).hexdigest()


@dataclass(frozen=True, slots=True)
class TaskIdBinding:
    candidate_task_id: CanonicalId
    persisted_task_id: CanonicalId

    def __post_init__(self) -> None:
        object.__setattr__(self, "candidate_task_id", _parse_id(
            self.candidate_task_id, kind=IdKind.TASK, field="candidate_task_id"
        ))
        object.__setattr__(self, "persisted_task_id", _parse_id(
            self.persisted_task_id, kind=IdKind.TASK, field="persisted_task_id"
        ))

    def to_dict(self) -> dict[str, str]:
        return {
            "candidate_task_id": self.candidate_task_id.value,
            "persisted_task_id": self.persisted_task_id.value,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "TaskIdBinding":
        if not isinstance(raw, Mapping) or frozenset(raw) != _BINDING_KEYS:
            raise ApprovedDAGConflictError("task binding payload is invalid")
        return cls(raw["candidate_task_id"], raw["persisted_task_id"])


@dataclass(frozen=True, slots=True)
class ApprovedDAGReceipt:
    project_id: CanonicalId
    requirement_version: int
    proposal_sha256: str
    task_bindings: tuple[TaskIdBinding, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "project_id", _parse_id(
            self.project_id, kind=IdKind.PROJECT, field="project_id"
        ))
        if isinstance(self.requirement_version, bool) or not isinstance(self.requirement_version, int) or self.requirement_version < 1:
            raise ApprovedDAGConflictError("requirement_version is invalid")
        if (
            not isinstance(self.proposal_sha256, str)
            or len(self.proposal_sha256) != 64
            or any(ch not in "0123456789abcdef" for ch in self.proposal_sha256)
        ):
            raise ApprovedDAGConflictError("proposal_sha256 is invalid")
        bindings = tuple(self.task_bindings)
        if not bindings or not all(isinstance(item, TaskIdBinding) for item in bindings):
            raise ApprovedDAGConflictError("task_bindings is invalid")
        candidates = [item.candidate_task_id.value for item in bindings]
        persisted = [item.persisted_task_id.value for item in bindings]
        if len(candidates) != len(set(candidates)) or len(persisted) != len(set(persisted)):
            raise ApprovedDAGConflictError("task_bindings must be one-to-one")
        object.__setattr__(self, "task_bindings", bindings)

    @property
    def task_ids(self) -> tuple[CanonicalId, ...]:
        return tuple(item.persisted_task_id for item in self.task_bindings)

    def mapping(self) -> dict[str, CanonicalId]:
        return {item.candidate_task_id.value: item.persisted_task_id for item in self.task_bindings}

    def to_dict(self) -> dict[str, object]:
        return {
            "format_version": APPROVED_DAG_RECEIPT_FORMAT_VERSION,
            "project_id": self.project_id.value,
            "requirement_version": self.requirement_version,
            "proposal_sha256": self.proposal_sha256,
            "task_bindings": [item.to_dict() for item in self.task_bindings],
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "ApprovedDAGReceipt":
        if not isinstance(raw, Mapping) or frozenset(raw) != _RECEIPT_KEYS:
            raise ApprovedDAGConflictError("approved DAG receipt keys are invalid")
        if raw["format_version"] != APPROVED_DAG_RECEIPT_FORMAT_VERSION:
            raise ApprovedDAGConflictError("unsupported approved DAG receipt format")
        bindings = raw["task_bindings"]
        if not isinstance(bindings, list):
            raise ApprovedDAGConflictError("receipt task_bindings must be an array")
        return cls(
            project_id=raw["project_id"],
            requirement_version=raw["requirement_version"],
            proposal_sha256=raw["proposal_sha256"],
            task_bindings=tuple(TaskIdBinding.from_dict(item) for item in bindings),
        )


def _record_for(task: ProposedTask, *, project_id: CanonicalId, mapping: Mapping[str, CanonicalId]) -> TaskRecord:
    try:
        return TaskRecord(
            task_id=mapping[task.task_id.value],
            project_id=project_id,
            title=task.title,
            role=task.role,
            priority=task.priority,
            dependencies=tuple(mapping[item.value] for item in task.dependencies),
            input_artifacts=task.input_artifacts,
            output_schema=task.output_schema,
            validators=task.validators,
            review_required=task.review_required,
            attempt=0,
            max_attempts=task.max_attempts,
            status=TaskState.CREATED,
        )
    except (KeyError, TaskRecordError) as exc:
        raise ApprovedDAGConflictError("approved proposal could not materialize a TaskRecord") from exc


def _receipt_path(handle, digest: str) -> Path:
    return handle.layout.data_directories["tasks"] / APPROVED_DAG_DIRECTORY_NAME / f"{digest}.json"


def _journal_path(handle) -> Path:
    return handle.layout.data_directories["tasks"] / APPROVED_DAG_DIRECTORY_NAME / APPROVED_DAG_JOURNAL_FILENAME


def _parse_receipt(path: Path) -> ApprovedDAGReceipt:
    return ApprovedDAGReceipt.from_dict(_read_json(path, label="approved DAG receipt"))


def _verify_receipt_matches_proposal(
    receipt: ApprovedDAGReceipt,
    proposal: TaskPlanProposal,
    project_store: ProjectStore,
) -> None:
    if receipt.project_id != proposal.project_id:
        raise ApprovedDAGConflictError("receipt project does not match proposal")
    if receipt.requirement_version != proposal.requirement_version:
        raise ApprovedDAGConflictError("receipt requirement version does not match proposal")
    if receipt.proposal_sha256 != _proposal_sha256(proposal):
        raise ApprovedDAGConflictError("receipt proposal digest does not match proposal")
    if tuple(item.candidate_task_id for item in receipt.task_bindings) != tuple(item.task_id for item in proposal.tasks):
        raise ApprovedDAGConflictError("receipt candidate-task order does not match proposal")

    mapping = receipt.mapping()
    store = TaskStore(project_store)
    for proposed in proposal.tasks:
        expected = _record_for(proposed, project_id=proposal.project_id, mapping=mapping)
        actual = store.open_task(proposal.project_id, expected.task_id)
        if actual != expected:
            raise ApprovedDAGConflictError(
                f"persisted task does not match approved proposal: {expected.task_id.value}"
            )


def _journal_payload(
    *,
    receipt: ApprovedDAGReceipt,
    previous_task_sequence: int,
    records: tuple[TaskRecord, ...],
) -> Mapping[str, object]:
    return {
        "format_version": APPROVED_DAG_JOURNAL_FORMAT_VERSION,
        "project_id": receipt.project_id.value,
        "requirement_version": receipt.requirement_version,
        "proposal_sha256": receipt.proposal_sha256,
        "previous_task_sequence": previous_task_sequence,
        "task_bindings": [item.to_dict() for item in receipt.task_bindings],
        "records": [item.to_dict() for item in records],
    }


def _recover_pending_journal(project_store: ProjectStore, handle) -> None:
    journal_path = _journal_path(handle)
    if not journal_path.exists() and not journal_path.is_symlink():
        return
    raw = _read_json(journal_path, label="approved DAG journal")
    if frozenset(raw) != _JOURNAL_KEYS or raw["format_version"] != APPROVED_DAG_JOURNAL_FORMAT_VERSION:
        raise ApprovedDAGConflictError("approved DAG journal format is invalid")
    if raw["project_id"] != handle.project_id.value:
        raise ApprovedDAGConflictError("approved DAG journal project mismatch")
    previous = raw["previous_task_sequence"]
    if isinstance(previous, bool) or not isinstance(previous, int) or previous < 0:
        raise ApprovedDAGConflictError("approved DAG journal sequence is invalid")
    bindings_raw = raw["task_bindings"]
    records_raw = raw["records"]
    if not isinstance(bindings_raw, list) or not isinstance(records_raw, list):
        raise ApprovedDAGConflictError("approved DAG journal arrays are invalid")
    receipt = ApprovedDAGReceipt(
        project_id=raw["project_id"],
        requirement_version=raw["requirement_version"],
        proposal_sha256=raw["proposal_sha256"],
        task_bindings=tuple(TaskIdBinding.from_dict(item) for item in bindings_raw),
    )
    try:
        records = tuple(TaskRecord.from_dict(item) for item in records_raw)
    except TaskRecordError as exc:
        raise ApprovedDAGConflictError("approved DAG journal records are invalid") from exc
    if tuple(item.task_id for item in records) != receipt.task_ids:
        raise ApprovedDAGConflictError("approved DAG journal task IDs do not match bindings")

    task_store = TaskStore(project_store)
    present: list[TaskRecord] = []
    missing = 0
    for expected in records:
        try:
            actual = task_store.open_task(handle.project_id, expected.task_id)
        except Exception as exc:
            from ai.tasks.store import TaskNotFoundError
            if isinstance(exc, TaskNotFoundError):
                missing += 1
                continue
            raise
        if actual != expected:
            raise ApprovedDAGConflictError("journal recovery found conflicting persisted task")
        present.append(actual)

    if present and missing:
        raise ApprovedDAGConflictError("approved DAG journal recovery found partial persistence")

    receipt_path = _receipt_path(handle, receipt.proposal_sha256)
    if present and not missing:
        raw_receipt = _canonical_json(receipt.to_dict(), label="approved DAG receipt")
        if receipt_path.exists() or receipt_path.is_symlink():
            if _parse_receipt(receipt_path) != receipt:
                raise ApprovedDAGConflictError("approved DAG receipt conflicts with recovery journal")
        else:
            _atomic_write_new(receipt_path, raw_receipt)
        journal_path.unlink()
        return

    with sqlite3.connect(project_store.database_path) as connection:
        verify_project_schema(connection)
        row = connection.execute(
            "SELECT last_sequence FROM canonical_id_sequences WHERE kind=?",
            (IdKind.TASK.value,),
        ).fetchone()
    if row != (previous,):
        raise ApprovedDAGConflictError(
            "rolled-back approved DAG journal cannot be retried because TASK sequence advanced"
        )
    journal_path.unlink()


def _insert_persisted_record(connection: sqlite3.Connection, record: TaskRecord) -> None:
    connection.execute(
        """
        INSERT INTO task_registry.tasks(task_id, project_id, status, record_json)
        VALUES (?, ?, ?, ?)
        """,
        (
            record.task_id.value,
            record.project_id.value,
            record.status.value,
            _canonical_record_json(record),
        ),
    )
    _insert_dependency_edges(
        connection,
        child_task_id=record.task_id,
        dependencies=record.dependencies,
        schema_prefix="task_registry",
    )


class ApprovedDAGStore:
    """O8.8 materializer for one validated ``TaskPlanProposal``.

    A proposal must pass the cumulative ten-check PlanValidator and must bind
    the latest APPROVED RequirementSet snapshot. Persistence allocates real
    global TASK IDs and inserts every TaskRecord plus dependency edge in one
    SQLite transaction. No task is scheduled or executed here.
    """

    def __init__(self, project_store: ProjectStore, *, validator: PlanValidator = DEFAULT_PLAN_VALIDATOR) -> None:
        if not isinstance(project_store, ProjectStore):
            raise TypeError("project_store must be ProjectStore")
        if not isinstance(validator, PlanValidator):
            raise TypeError("validator must be PlanValidator")
        self._projects = project_store
        self._validator = validator

    def persist(self, proposal: TaskPlanProposal) -> ApprovedDAGReceipt:
        if not isinstance(proposal, TaskPlanProposal):
            raise TypeError("proposal must be TaskPlanProposal")

        result = self._validator.validate(proposal)
        if self._validator.check_ids != PLAN_VALIDATOR_CHECK_IDS or len(result.checks) != len(PLAN_VALIDATOR_CHECK_IDS):
            raise ApprovedDAGValidationError("PlanValidator check set is not the frozen O8.7 set")
        if result.overall is not ValidationStatus.PASS:
            failed = tuple(check.check_id for check in result.checks if check.status is ValidationStatus.FAIL)
            raise ApprovedDAGValidationError(
                "TaskPlanProposal is not approved by PlanValidator: " + ",".join(failed)
            )

        try:
            latest = RequirementVersionStore(self._projects).open_latest(proposal.project_id)
        except (RequirementVersionConflictError, RequirementVersionNotFoundError) as exc:
            raise ApprovedDAGValidationError(f"requirement snapshot could not be opened: {exc}") from exc
        if latest.version != proposal.requirement_version:
            raise ApprovedDAGValidationError("proposal requirement_version is not the latest Project State version")
        if latest.requirement_set.status is not RequirementSetStatus.APPROVED:
            raise ApprovedDAGValidationError("proposal requirement snapshot is not APPROVED")

        handle = TaskStore(self._projects)._open_handle(proposal.project_id)
        approved_dir = handle.layout.data_directories["tasks"] / APPROVED_DAG_DIRECTORY_NAME
        if approved_dir.is_symlink():
            raise ApprovedDAGConflictError("approved DAG directory must not be a symbolic link")
        approved_dir.mkdir(parents=True, exist_ok=True)
        if approved_dir.is_symlink() or not approved_dir.is_dir():
            raise ApprovedDAGConflictError("approved DAG directory is invalid")

        _recover_pending_journal(self._projects, handle)

        digest = _proposal_sha256(proposal)
        receipt_path = _receipt_path(handle, digest)
        if receipt_path.exists() or receipt_path.is_symlink():
            receipt = _parse_receipt(receipt_path)
            _verify_receipt_matches_proposal(receipt, proposal, self._projects)
            return receipt

        registry_path = _registry_path(handle)
        initialize_task_registry(registry_path)
        try:
            TaskDAGValidator(self._projects).validate_project(proposal.project_id)
        except (TaskDAGConflictError, TaskDAGCycleError) as exc:
            raise ApprovedDAGConflictError(f"existing persisted task graph is invalid: {exc}") from exc

        connection = sqlite3.connect(handle.database_path)
        journal_path = _journal_path(handle)
        receipt: ApprovedDAGReceipt | None = None
        records: tuple[TaskRecord, ...] = ()
        committed = False
        try:
            verify_project_schema(connection)
            _attach_registry(connection, registry_path)
            _verify_attached_registry(connection)
            connection.execute("BEGIN IMMEDIATE")

            if journal_path.exists() or journal_path.is_symlink():
                raise ApprovedDAGConflictError("pending approved DAG journal exists")
            if receipt_path.exists() or receipt_path.is_symlink():
                connection.rollback()
                receipt = _parse_receipt(receipt_path)
                _verify_receipt_matches_proposal(receipt, proposal, self._projects)
                return receipt

            row = connection.execute(
                "SELECT last_sequence FROM canonical_id_sequences WHERE kind=?",
                (IdKind.TASK.value,),
            ).fetchone()
            if row is None or isinstance(row[0], bool) or not isinstance(row[0], int) or row[0] < 0:
                raise ApprovedDAGConflictError("TASK canonical ID sequence is invalid")
            previous_sequence = row[0]

            persisted_ids: list[CanonicalId] = []
            for offset in range(1, len(proposal.tasks) + 1):
                try:
                    persisted_ids.append(CanonicalId.from_sequence(IdKind.TASK, previous_sequence + offset))
                except CanonicalIdError as exc:
                    raise ApprovedDAGConflictError("TASK canonical ID sequence is exhausted") from exc
            bindings = tuple(
                TaskIdBinding(task.task_id, persisted)
                for task, persisted in zip(proposal.tasks, persisted_ids, strict=True)
            )
            receipt = ApprovedDAGReceipt(
                project_id=proposal.project_id,
                requirement_version=proposal.requirement_version,
                proposal_sha256=digest,
                task_bindings=bindings,
            )
            mapping = receipt.mapping()
            records = tuple(
                _record_for(task, project_id=proposal.project_id, mapping=mapping)
                for task in proposal.tasks
            )

            _atomic_write_new(
                journal_path,
                _canonical_json(
                    _journal_payload(
                        receipt=receipt,
                        previous_task_sequence=previous_sequence,
                        records=records,
                    ),
                    label="approved DAG journal",
                ),
            )

            for record in records:
                _insert_persisted_record(connection, record)

            next_sequence = previous_sequence + len(records)
            connection.execute(
                "UPDATE canonical_id_sequences SET last_sequence=? WHERE kind=?",
                (next_sequence, IdKind.TASK.value),
            )
            if connection.execute("SELECT changes()").fetchone()[0] != 1:
                raise ApprovedDAGConflictError("TASK canonical ID sequence update failed")

            connection.commit()
            committed = True
        except ProjectSchemaError as exc:
            if connection.in_transaction:
                connection.rollback()
            raise ApprovedDAGConflictError(f"Project State schema is invalid: {exc}") from exc
        except Exception:
            if connection.in_transaction:
                connection.rollback()
            if not committed:
                journal_path.unlink(missing_ok=True)
            raise
        finally:
            connection.close()

        assert receipt is not None
        try:
            _atomic_write_new(
                receipt_path,
                _canonical_json(receipt.to_dict(), label="approved DAG receipt"),
            )
            journal_path.unlink()
        except Exception:
            # The SQLite commit is already durable. Keep the prepared journal so
            # the next process can verify/finalize the same receipt without
            # allocating new TASK IDs.
            raise

        _verify_receipt_matches_proposal(receipt, proposal, self._projects)
        try:
            TaskDAGValidator(self._projects).validate_project(proposal.project_id)
        except (TaskDAGConflictError, TaskDAGCycleError) as exc:
            raise ApprovedDAGConflictError(f"persisted approved DAG verification failed: {exc}") from exc
        return receipt


def persist_approved_task_dag(
    project_store: ProjectStore,
    proposal: TaskPlanProposal,
    *,
    validator: PlanValidator = DEFAULT_PLAN_VALIDATOR,
) -> ApprovedDAGReceipt:
    """Persist one fully validated proposal without scheduling any task."""

    return ApprovedDAGStore(project_store, validator=validator).persist(proposal)
