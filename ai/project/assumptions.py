from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
import os
from pathlib import Path
import re
from types import MappingProxyType
from typing import Any, Mapping, TypeAlias
import tempfile

from ai.contracts.ids import CanonicalId, CanonicalIdError, IdKind
from ai.contracts.provenance import (
    ProvenanceClass,
    ProvenanceError,
    parse_provenance_class,
)
from ai.project.store import (
    ProjectHandle,
    ProjectNotFoundError,
    ProjectStore,
    ProjectStoreConflictError,
)


ASSUMPTION_CONTRACT_VERSION = "1.0"
ASSUMPTION_STORE_CONTRACT_VERSION = "1.0"
ASSUMPTION_FORMAT_VERSION = 1
ASSUMPTION_FILENAME = "assumptions.json"
ASSUMPTION_HASH_FILENAME = "assumptions.json.sha256"
MAX_ASSUMPTIONS_PER_SET = 256
MAX_ASSUMPTION_KEY_CHARS = 128
MAX_ASSUMPTION_JSON_DEPTH = 16
MAX_ASSUMPTION_STORE_BYTES = 2 * 1024 * 1024

_ASSUMPTION_KEY_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,127}$")
_ASSUMPTION_KEYS = frozenset({"key", "value", "provenance"})
_ASSUMPTION_SET_KEYS = frozenset({"project_id", "assumptions"})
_ASSUMPTION_FILE_KEYS = frozenset(
    {"format_version", "project_id", "assumptions"}
)
_HASH_RE = re.compile(r"^[0-9a-f]{64}$")

JSONScalar: TypeAlias = None | bool | int | float | str
JSONValue: TypeAlias = (
    JSONScalar
    | tuple["JSONValue", ...]
    | Mapping[str, "JSONValue"]
)


class AssumptionContractError(ValueError):
    """Raised when an engineering assumption violates S3.7."""


class AssumptionStoreError(RuntimeError):
    """Base error for persistent S3.7 assumption storage."""


class AssumptionStoreConflictError(AssumptionStoreError):
    """Raised when assumption storage is partial, corrupt, or mismatched."""


class AssumptionSetNotFoundError(AssumptionStoreError):
    """Raised when a project has no persisted S3.7 assumption set."""


def _parse_project_id(raw: CanonicalId | str) -> CanonicalId:
    try:
        value = raw if isinstance(raw, CanonicalId) else CanonicalId.parse(raw)
    except (CanonicalIdError, TypeError) as exc:
        raise AssumptionContractError(
            "project_id must be a valid canonical project ID"
        ) from exc
    if value.kind is not IdKind.PROJECT:
        raise AssumptionContractError(
            "project_id must be a canonical project ID"
        )
    return value


def _require_key(raw: Any) -> str:
    if not isinstance(raw, str):
        raise AssumptionContractError("assumption key must be a string")
    if not raw or raw != raw.strip():
        raise AssumptionContractError(
            "assumption key must be clean non-empty text"
        )
    if (
        len(raw) > MAX_ASSUMPTION_KEY_CHARS
        or _ASSUMPTION_KEY_RE.fullmatch(raw) is None
    ):
        raise AssumptionContractError(
            "assumption key must match [A-Za-z][A-Za-z0-9_.-]{0,127}"
        )
    return raw


def _parse_assumption_provenance(
    raw: ProvenanceClass | str,
) -> ProvenanceClass:
    try:
        value = parse_provenance_class(raw)
    except (ProvenanceError, TypeError) as exc:
        raise AssumptionContractError(
            "assumption provenance is invalid"
        ) from exc

    if value is not ProvenanceClass.ENGINEERING_ASSUMPTION:
        raise AssumptionContractError(
            "assumption provenance must be ENGINEERING_ASSUMPTION"
        )
    return value


def _freeze_json(
    value: Any,
    *,
    path: str,
    depth: int = 0,
) -> JSONValue:
    if depth > MAX_ASSUMPTION_JSON_DEPTH:
        raise AssumptionContractError(
            f"{path} exceeds MAX_ASSUMPTION_JSON_DEPTH"
        )
    if value is None or isinstance(value, (bool, str)):
        return value
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise AssumptionContractError(
                f"{path} contains a non-finite number"
            )
        return value
    if isinstance(value, (list, tuple)):
        return tuple(
            _freeze_json(
                item,
                path=f"{path}[{index}]",
                depth=depth + 1,
            )
            for index, item in enumerate(value)
        )
    if isinstance(value, Mapping):
        frozen: dict[str, JSONValue] = {}
        for key, item in value.items():
            if not isinstance(key, str) or not key:
                raise AssumptionContractError(
                    f"{path} contains an invalid object key"
                )
            if key in frozen:
                raise AssumptionContractError(
                    f"{path} contains a duplicate object key"
                )
            frozen[key] = _freeze_json(
                item,
                path=f"{path}.{key}",
                depth=depth + 1,
            )
        return MappingProxyType(frozen)

    raise AssumptionContractError(
        f"{path} contains unsupported JSON value type "
        f"{type(value).__name__}"
    )


def _thaw_json(value: JSONValue) -> Any:
    if isinstance(value, tuple):
        return [_thaw_json(item) for item in value]
    if isinstance(value, Mapping):
        return {
            key: _thaw_json(item)
            for key, item in value.items()
        }
    return value


@dataclass(frozen=True)
class Assumption:
    """One explicit engineering assumption.

    S3.7 stores classification only. An assumption does not become a fact,
    verified fact, decision, or proposal because it is persisted here.
    """

    key: str
    value: JSONValue
    provenance: ProvenanceClass = ProvenanceClass.ENGINEERING_ASSUMPTION

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "key",
            _require_key(self.key),
        )
        object.__setattr__(
            self,
            "value",
            _freeze_json(
                self.value,
                path=f"assumption[{self.key}].value",
            ),
        )
        object.__setattr__(
            self,
            "provenance",
            _parse_assumption_provenance(self.provenance),
        )

    @classmethod
    def from_dict(
        cls,
        raw: Mapping[str, Any],
    ) -> "Assumption":
        if not isinstance(raw, Mapping):
            raise AssumptionContractError(
                "assumption payload must be an object"
            )
        if frozenset(raw.keys()) != _ASSUMPTION_KEYS:
            raise AssumptionContractError(
                "assumption payload must contain exactly "
                "key,value,provenance"
            )
        return cls(
            key=raw["key"],
            value=raw["value"],
            provenance=raw["provenance"],
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "value": _thaw_json(self.value),
            "provenance": self.provenance.value,
        }


@dataclass(frozen=True)
class AssumptionSet:
    project_id: CanonicalId
    assumptions: tuple[Assumption, ...]

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "project_id",
            _parse_project_id(self.project_id),
        )

        if not isinstance(self.assumptions, tuple):
            try:
                object.__setattr__(
                    self,
                    "assumptions",
                    tuple(self.assumptions),
                )
            except TypeError as exc:
                raise AssumptionContractError(
                    "assumptions must be an iterable of Assumption"
                ) from exc

        if len(self.assumptions) > MAX_ASSUMPTIONS_PER_SET:
            raise AssumptionContractError(
                "assumptions exceeds MAX_ASSUMPTIONS_PER_SET"
            )

        seen: set[str] = set()
        for index, item in enumerate(self.assumptions):
            if not isinstance(item, Assumption):
                raise AssumptionContractError(
                    f"assumptions[{index}] must be Assumption"
                )
            if item.key in seen:
                raise AssumptionContractError(
                    f"duplicate assumption key: {item.key}"
                )
            seen.add(item.key)

    @classmethod
    def from_dict(
        cls,
        raw: Mapping[str, Any],
    ) -> "AssumptionSet":
        if not isinstance(raw, Mapping):
            raise AssumptionContractError(
                "AssumptionSet payload must be an object"
            )
        if frozenset(raw.keys()) != _ASSUMPTION_SET_KEYS:
            raise AssumptionContractError(
                "AssumptionSet payload must contain exactly "
                "project_id,assumptions"
            )
        raw_assumptions = raw["assumptions"]
        if not isinstance(raw_assumptions, (list, tuple)):
            raise AssumptionContractError(
                "assumptions must be an array"
            )
        if len(raw_assumptions) > MAX_ASSUMPTIONS_PER_SET:
            raise AssumptionContractError(
                "assumptions exceeds MAX_ASSUMPTIONS_PER_SET"
            )

        return cls(
            project_id=raw["project_id"],
            assumptions=tuple(
                Assumption.from_dict(item)
                for item in raw_assumptions
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "project_id": self.project_id.value,
            "assumptions": [
                item.to_dict()
                for item in self.assumptions
            ],
        }


def _reject_json_constant(raw: str) -> None:
    raise AssumptionStoreConflictError(
        f"non-finite JSON constant is forbidden: {raw}"
    )


def _object_without_duplicates(
    pairs: list[tuple[str, Any]],
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise AssumptionStoreConflictError(
                f"duplicate JSON key: {key}"
            )
        result[key] = value
    return result


def _load_json_object(
    raw: bytes,
    *,
    label: str,
) -> dict[str, Any]:
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise AssumptionStoreConflictError(
            f"{label} is not UTF-8"
        ) from exc
    try:
        payload = json.loads(
            text,
            object_pairs_hook=_object_without_duplicates,
            parse_constant=_reject_json_constant,
        )
    except AssumptionStoreConflictError:
        raise
    except json.JSONDecodeError as exc:
        raise AssumptionStoreConflictError(
            f"{label} is not valid JSON"
        ) from exc

    if not isinstance(payload, dict):
        raise AssumptionStoreConflictError(
            f"{label} top level must be an object"
        )
    return payload


def _read_bounded(
    path: Path,
    *,
    limit: int,
    label: str,
) -> bytes:
    if path.is_symlink():
        raise AssumptionStoreConflictError(
            f"{label} must not be a symbolic link"
        )
    if not path.exists():
        raise AssumptionSetNotFoundError(
            f"{label} does not exist"
        )
    if not path.is_file():
        raise AssumptionStoreConflictError(
            f"{label} is not a regular file"
        )
    try:
        with path.open("rb") as stream:
            raw = stream.read(limit + 1)
    except OSError as exc:
        raise AssumptionStoreConflictError(
            f"{label} could not be read"
        ) from exc
    if len(raw) > limit:
        raise AssumptionStoreConflictError(
            f"{label} exceeds its size limit"
        )
    return raw


def _encode(
    payload: Mapping[str, Any],
    *,
    limit: int,
) -> bytes:
    encoded = (
        json.dumps(
            dict(payload),
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")
    if len(encoded) > limit:
        raise AssumptionStoreConflictError(
            "serialized assumption set exceeds size limit"
        )
    return encoded


def _atomic_replace(path: Path, raw: bytes) -> None:
    if path.is_symlink():
        raise AssumptionStoreConflictError(
            f"{path.name} must not be a symbolic link"
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        dir=str(path.parent),
    )
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def _hash_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _read_hash(path: Path) -> str:
    raw = _read_bounded(
        path,
        limit=128,
        label="assumption-set hash",
    )
    try:
        text = raw.decode("ascii")
    except UnicodeDecodeError as exc:
        raise AssumptionStoreConflictError(
            "assumption-set hash is not ASCII"
        ) from exc
    if not text.endswith("\n"):
        raise AssumptionStoreConflictError(
            "assumption-set hash is not canonical"
        )
    digest = text[:-1]
    if _HASH_RE.fullmatch(digest) is None:
        raise AssumptionStoreConflictError(
            "assumption-set hash is invalid"
        )
    return digest


class AssumptionStore:
    """Persist one current AssumptionSet per project.

    S3.7 does not version assumptions and does not promote them to facts.
    Reusable multi-record transactions remain S3.9.
    """

    def __init__(self, project_store: ProjectStore) -> None:
        if not isinstance(project_store, ProjectStore):
            raise TypeError("project_store must be ProjectStore")
        self._project_store = project_store

    def _open_handle(
        self,
        project_id: CanonicalId | str,
    ) -> ProjectHandle:
        try:
            canonical = _parse_project_id(project_id)
            return self._project_store.open_project(canonical)
        except AssumptionContractError as exc:
            raise AssumptionStoreConflictError(str(exc)) from exc
        except (
            ProjectNotFoundError,
            ProjectStoreConflictError,
        ) as exc:
            raise AssumptionStoreConflictError(
                f"project could not be opened: {exc}"
            ) from exc

    def _paths(
        self,
        handle: ProjectHandle,
    ) -> tuple[Path, Path]:
        return (
            handle.layout.project_dir / ASSUMPTION_FILENAME,
            handle.layout.project_dir / ASSUMPTION_HASH_FILENAME,
        )

    def save(
        self,
        assumption_set: AssumptionSet,
    ) -> AssumptionSet:
        if not isinstance(assumption_set, AssumptionSet):
            raise TypeError(
                "assumption_set must be AssumptionSet"
            )

        handle = self._open_handle(
            assumption_set.project_id
        )
        if handle.project_id != assumption_set.project_id:
            raise AssumptionStoreConflictError(
                "AssumptionSet project ID mismatch"
            )

        assumption_path, hash_path = self._paths(handle)
        payload = {
            "format_version": ASSUMPTION_FORMAT_VERSION,
            "project_id": assumption_set.project_id.value,
            "assumptions": [
                item.to_dict()
                for item in assumption_set.assumptions
            ],
        }
        raw = _encode(
            payload,
            limit=MAX_ASSUMPTION_STORE_BYTES,
        )
        digest = _hash_bytes(raw)
        hash_raw = (digest + "\n").encode("ascii")

        old_assumption = (
            assumption_path.read_bytes()
            if assumption_path.is_file()
            and not assumption_path.is_symlink()
            else None
        )
        old_hash = (
            hash_path.read_bytes()
            if hash_path.is_file()
            and not hash_path.is_symlink()
            else None
        )
        if assumption_path.is_symlink() or hash_path.is_symlink():
            raise AssumptionStoreConflictError(
                "assumption store paths must not be symbolic links"
            )

        try:
            _atomic_replace(assumption_path, raw)
            _atomic_replace(hash_path, hash_raw)
        except Exception:
            try:
                if old_assumption is None:
                    assumption_path.unlink(missing_ok=True)
                else:
                    _atomic_replace(
                        assumption_path,
                        old_assumption,
                    )
                if old_hash is None:
                    hash_path.unlink(missing_ok=True)
                else:
                    _atomic_replace(
                        hash_path,
                        old_hash,
                    )
            except Exception:
                pass
            raise

        return assumption_set

    def open(
        self,
        project_id: CanonicalId | str,
    ) -> AssumptionSet:
        handle = self._open_handle(project_id)
        assumption_path, hash_path = self._paths(handle)

        raw = _read_bounded(
            assumption_path,
            limit=MAX_ASSUMPTION_STORE_BYTES,
            label="assumption set",
        )
        expected = _read_hash(hash_path)
        actual = _hash_bytes(raw)
        if actual != expected:
            raise AssumptionStoreConflictError(
                "assumption-set hash mismatch"
            )

        payload = _load_json_object(
            raw,
            label="assumption set",
        )
        if frozenset(payload.keys()) != _ASSUMPTION_FILE_KEYS:
            raise AssumptionStoreConflictError(
                "assumption-set file keys do not match S3.7 contract"
            )
        if (
            payload["format_version"]
            != ASSUMPTION_FORMAT_VERSION
        ):
            raise AssumptionStoreConflictError(
                "unsupported assumption-set format"
            )
        if payload["project_id"] != handle.project_id.value:
            raise AssumptionStoreConflictError(
                "assumption-set project ID mismatch"
            )

        try:
            return AssumptionSet.from_dict(
                {
                    "project_id": payload["project_id"],
                    "assumptions": payload["assumptions"],
                }
            )
        except AssumptionContractError as exc:
            raise AssumptionStoreConflictError(
                f"stored AssumptionSet is invalid: {exc}"
            ) from exc
