from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import tempfile
from typing import BinaryIO

from ai.contracts.ids import CanonicalId, CanonicalIdError, IdKind
from ai.project.store import (
    ProjectNotFoundError,
    ProjectStore,
    ProjectStoreConflictError,
)


ARTIFACT_HASH_CONTRACT_VERSION = "1.0"
ARTIFACT_CONTENT_STORE_DIRNAME = "artifact_content"
ARTIFACT_CONTENT_ALGORITHM = "sha256"
MAX_ARTIFACT_CONTENT_BYTES = 64 * 1024 * 1024
ARTIFACT_HASH_CHUNK_BYTES = 1024 * 1024

_SHA256_HEX_LENGTH = 64


class ArtifactHashError(RuntimeError):
    """Base error for A4.4 artifact content hashing."""


class ArtifactHashMismatchError(ArtifactHashError):
    """Raised when artifact content does not match its recorded SHA-256."""


class ArtifactContentNotFoundError(ArtifactHashError):
    """Raised when the content-addressed artifact blob is missing."""


class ArtifactContentStoreConflictError(ArtifactHashError):
    """Raised when the artifact content store is unsafe or corrupt."""


@dataclass(frozen=True)
class ArtifactContentRef:
    sha256: str
    size_bytes: int
    created_new: bool

    def __post_init__(self) -> None:
        if (
            not isinstance(self.sha256, str)
            or len(self.sha256) != _SHA256_HEX_LENGTH
            or any(ch not in "0123456789abcdef" for ch in self.sha256)
        ):
            raise ArtifactContentStoreConflictError(
                "ArtifactContentRef sha256 is invalid"
            )
        if (
            isinstance(self.size_bytes, bool)
            or not isinstance(self.size_bytes, int)
            or not 0 <= self.size_bytes <= MAX_ARTIFACT_CONTENT_BYTES
        ):
            raise ArtifactContentStoreConflictError(
                "ArtifactContentRef size is invalid"
            )
        if not isinstance(self.created_new, bool):
            raise ArtifactContentStoreConflictError(
                "ArtifactContentRef created_new must be boolean"
            )


def _parse_project_id(raw: CanonicalId | str) -> CanonicalId:
    try:
        value = raw if isinstance(raw, CanonicalId) else CanonicalId.parse(raw)
    except (CanonicalIdError, TypeError) as exc:
        raise ArtifactContentStoreConflictError(
            "project_id must be canonical"
        ) from exc
    if value.kind is not IdKind.PROJECT:
        raise ArtifactContentStoreConflictError(
            "project_id must identify a project"
        )
    return value


def _coerce_content(raw: bytes | bytearray | memoryview) -> bytes:
    if isinstance(raw, bytes):
        data = raw
    elif isinstance(raw, (bytearray, memoryview)):
        data = bytes(raw)
    else:
        raise TypeError("artifact content must be bytes-like")

    if len(data) > MAX_ARTIFACT_CONTENT_BYTES:
        raise ArtifactContentStoreConflictError(
            "artifact content exceeds MAX_ARTIFACT_CONTENT_BYTES"
        )
    return data


def compute_artifact_sha256(
    content: bytes | bytearray | memoryview,
) -> str:
    data = _coerce_content(content)
    return hashlib.sha256(data).hexdigest()


def _hash_stream(
    stream: BinaryIO,
    *,
    max_bytes: int = MAX_ARTIFACT_CONTENT_BYTES,
) -> tuple[str, int]:
    digest = hashlib.sha256()
    total = 0

    while True:
        chunk = stream.read(ARTIFACT_HASH_CHUNK_BYTES)
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise ArtifactContentStoreConflictError(
                "artifact content exceeds bounded size during hashing"
            )
        digest.update(chunk)

    return digest.hexdigest(), total


class ArtifactContentStore:
    """Bounded content-addressed filesystem storage for artifact payload bytes.

    Layout:

      <project>/artifact_content/sha256/ab/<64-hex>.blob

    The blob name is its SHA-256, so identical content is naturally deduplicated
    within a project. Existing blobs are never overwritten.
    """

    def __init__(self, project_store: ProjectStore) -> None:
        if not isinstance(project_store, ProjectStore):
            raise TypeError("project_store must be ProjectStore")
        self._project_store = project_store

    def _project_dir(
        self,
        project_id: CanonicalId | str,
    ) -> Path:
        project = _parse_project_id(project_id)
        try:
            handle = self._project_store.open_project(project)
        except (ProjectNotFoundError, ProjectStoreConflictError) as exc:
            raise ArtifactContentStoreConflictError(
                f"project could not be opened: {exc}"
            ) from exc
        return handle.layout.project_dir

    def root(
        self,
        project_id: CanonicalId | str,
    ) -> Path:
        return (
            self._project_dir(project_id)
            / ARTIFACT_CONTENT_STORE_DIRNAME
            / ARTIFACT_CONTENT_ALGORITHM
        )

    def blob_path(
        self,
        project_id: CanonicalId | str,
        sha256: str,
    ) -> Path:
        if (
            not isinstance(sha256, str)
            or len(sha256) != _SHA256_HEX_LENGTH
            or any(ch not in "0123456789abcdef" for ch in sha256)
        ):
            raise ArtifactContentStoreConflictError(
                "sha256 must be 64 lowercase hexadecimal characters"
            )
        root = self.root(project_id)
        return root / sha256[:2] / f"{sha256}.blob"

    def put(
        self,
        project_id: CanonicalId | str,
        content: bytes | bytearray | memoryview,
    ) -> ArtifactContentRef:
        data = _coerce_content(content)
        digest = hashlib.sha256(data).hexdigest()
        path = self.blob_path(project_id, digest)

        root = self.root(project_id)
        if root.is_symlink():
            raise ArtifactContentStoreConflictError(
                "artifact content root must not be a symbolic link"
            )
        root.mkdir(parents=True, exist_ok=True)

        bucket = path.parent
        if bucket.is_symlink():
            raise ArtifactContentStoreConflictError(
                "artifact content bucket must not be a symbolic link"
            )
        bucket.mkdir(mode=0o700, parents=True, exist_ok=True)

        if path.is_symlink():
            raise ArtifactContentStoreConflictError(
                "artifact content blob must not be a symbolic link"
            )

        if path.exists():
            existing_digest, existing_size = self._verify_path(path, digest)
            return ArtifactContentRef(
                sha256=existing_digest,
                size_bytes=existing_size,
                created_new=False,
            )

        fd, tmp_name = tempfile.mkstemp(
            prefix=f".{digest}.",
            dir=str(bucket),
        )
        tmp = Path(tmp_name)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.chmod(tmp, 0o600)

            # Local-first runtime is single-writer by default. Still fail closed
            # if another writer created a conflicting path before replacement.
            if path.exists() or path.is_symlink():
                if path.is_symlink():
                    raise ArtifactContentStoreConflictError(
                        "artifact content blob became a symbolic link"
                    )
                existing_digest, existing_size = self._verify_path(path, digest)
                return ArtifactContentRef(
                    sha256=existing_digest,
                    size_bytes=existing_size,
                    created_new=False,
                )

            os.replace(tmp, path)
            self._fsync_dir(bucket)
            return ArtifactContentRef(
                sha256=digest,
                size_bytes=len(data),
                created_new=True,
            )
        finally:
            tmp.unlink(missing_ok=True)

    def _verify_path(
        self,
        path: Path,
        expected_sha256: str,
    ) -> tuple[str, int]:
        if path.is_symlink():
            raise ArtifactContentStoreConflictError(
                "artifact content blob must not be a symbolic link"
            )
        if not path.exists():
            raise ArtifactContentNotFoundError(
                f"artifact content blob does not exist: {expected_sha256}"
            )
        if not path.is_file():
            raise ArtifactContentStoreConflictError(
                "artifact content blob is not a regular file"
            )

        try:
            with path.open("rb") as stream:
                actual, size = _hash_stream(stream)
        except OSError as exc:
            raise ArtifactContentStoreConflictError(
                "artifact content blob could not be read"
            ) from exc

        if actual != expected_sha256:
            raise ArtifactHashMismatchError(
                "artifact content SHA-256 mismatch"
            )
        return actual, size

    def verify(
        self,
        project_id: CanonicalId | str,
        expected_sha256: str,
    ) -> ArtifactContentRef:
        path = self.blob_path(project_id, expected_sha256)
        actual, size = self._verify_path(path, expected_sha256)
        return ArtifactContentRef(
            sha256=actual,
            size_bytes=size,
            created_new=False,
        )

    def read(
        self,
        project_id: CanonicalId | str,
        expected_sha256: str,
    ) -> bytes:
        path = self.blob_path(project_id, expected_sha256)
        self._verify_path(path, expected_sha256)

        try:
            with path.open("rb") as stream:
                raw = stream.read(MAX_ARTIFACT_CONTENT_BYTES + 1)
        except OSError as exc:
            raise ArtifactContentStoreConflictError(
                "artifact content blob could not be read"
            ) from exc

        if len(raw) > MAX_ARTIFACT_CONTENT_BYTES:
            raise ArtifactContentStoreConflictError(
                "artifact content exceeds bounded size"
            )
        return raw

    def discard_created(
        self,
        project_id: CanonicalId | str,
        ref: ArtifactContentRef,
    ) -> None:
        """Best-effort cleanup for an uncommitted newly-created blob.

        This helper only removes blobs created by the current write attempt.
        Content that was already present (deduplicated) is never removed.
        """

        if not ref.created_new:
            return
        path = self.blob_path(project_id, ref.sha256)
        if path.is_symlink():
            raise ArtifactContentStoreConflictError(
                "artifact content blob must not be a symbolic link"
            )
        path.unlink(missing_ok=True)
        self._fsync_dir(path.parent)

    @staticmethod
    def _fsync_dir(path: Path) -> None:
        flags = getattr(os, "O_DIRECTORY", 0) | os.O_RDONLY
        try:
            fd = os.open(path, flags)
        except OSError:
            return
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
