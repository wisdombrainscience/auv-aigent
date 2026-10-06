from __future__ import annotations

from pathlib import Path

import pytest

from ai.artifacts import (
    ARTIFACT_CONTENT_ALGORITHM,
    ARTIFACT_CONTENT_STORE_DIRNAME,
    ARTIFACT_HASH_CHUNK_BYTES,
    ARTIFACT_HASH_CONTRACT_VERSION,
    MAX_ARTIFACT_CONTENT_BYTES,
    ArtifactContentStore,
    ArtifactContentStoreConflictError,
    ArtifactHashMismatchError,
    ArtifactVersionInput,
    ArtifactVersionStore,
    compute_artifact_sha256,
)
from ai.project.store import ProjectStore


def make_project(tmp_path: Path):
    projects = ProjectStore(
        tmp_path / "remote-uav" / "projects"
    )
    handle = projects.create_project()
    return projects, handle


def input_for(content: bytes) -> ArtifactVersionInput:
    return ArtifactVersionInput(
        type="mass_budget",
        producer_task="TASK-000001",
        input_dependency_versions=(),
        content=content,
        created_timestamp="2026-10-03T22:15:00Z",
    )


def test_hash_contract_and_bounds_are_explicit() -> None:
    assert ARTIFACT_HASH_CONTRACT_VERSION == "1.0"
    assert ARTIFACT_CONTENT_STORE_DIRNAME == "artifact_content"
    assert ARTIFACT_CONTENT_ALGORITHM == "sha256"
    assert MAX_ARTIFACT_CONTENT_BYTES == 64 * 1024 * 1024
    assert ARTIFACT_HASH_CHUNK_BYTES == 1024 * 1024


def test_compute_sha256_is_exact_and_deterministic() -> None:
    assert (
        compute_artifact_sha256(b"abc")
        == "ba7816bf8f01cfea414140de5dae2223"
           "b00361a396177a9cb410ff61f20015ad"
    )


def test_content_store_is_content_addressed_and_deduplicated(
    tmp_path: Path,
) -> None:
    projects, handle = make_project(tmp_path)
    store = ArtifactContentStore(projects)

    first = store.put(handle.project_id, b"same")
    second = store.put(handle.project_id, b"same")

    assert first.sha256 == second.sha256
    assert first.created_new is True
    assert second.created_new is False
    assert store.read(handle.project_id, first.sha256) == b"same"


def test_content_path_is_derived_only_from_hash(tmp_path: Path) -> None:
    projects, handle = make_project(tmp_path)
    store = ArtifactContentStore(projects)
    ref = store.put(handle.project_id, b"payload")

    path = store.blob_path(handle.project_id, ref.sha256)
    assert path.name == f"{ref.sha256}.blob"
    assert path.parent.name == ref.sha256[:2]
    assert "sha256" in path.parts


def test_artifact_version_store_computes_record_hash_from_content(
    tmp_path: Path,
) -> None:
    projects, handle = make_project(tmp_path)
    versions = ArtifactVersionStore(projects)
    content = b"mass-budget-v1"

    record = versions.create_artifact(
        handle.project_id,
        input_for(content),
    )

    assert record.hash == compute_artifact_sha256(content)


def test_open_version_verifies_content_after_restart(tmp_path: Path) -> None:
    projects, handle = make_project(tmp_path)
    versions = ArtifactVersionStore(projects)
    content = b"mass-budget-v1"
    record = versions.create_artifact(
        handle.project_id,
        input_for(content),
    )

    restarted = ArtifactVersionStore(
        ProjectStore(projects.projects_root)
    )
    assert restarted.open_version(
        handle.project_id,
        record.artifact_id,
        record.version,
    ) == record


def test_tampered_blob_fails_closed(tmp_path: Path) -> None:
    projects, handle = make_project(tmp_path)
    versions = ArtifactVersionStore(projects)
    content_store = ArtifactContentStore(projects)
    record = versions.create_artifact(
        handle.project_id,
        input_for(b"original"),
    )

    path = content_store.blob_path(
        handle.project_id,
        record.hash,
    )
    path.write_bytes(b"tampered")

    with pytest.raises(
        Exception,
        match="content integrity failure",
    ):
        versions.open_version(
            handle.project_id,
            record.artifact_id,
            record.version,
        )


def test_symlink_blob_fails_closed(tmp_path: Path) -> None:
    projects, handle = make_project(tmp_path)
    versions = ArtifactVersionStore(projects)
    content_store = ArtifactContentStore(projects)
    record = versions.create_artifact(
        handle.project_id,
        input_for(b"original"),
    )
    path = content_store.blob_path(handle.project_id, record.hash)
    external = tmp_path / "external.bin"
    external.write_bytes(b"original")
    path.unlink()
    path.symlink_to(external)

    with pytest.raises(Exception, match="content integrity failure"):
        versions.open_version(
            handle.project_id,
            record.artifact_id,
            record.version,
        )


def test_oversized_content_is_rejected_without_truncation(
    tmp_path: Path,
) -> None:
    projects, handle = make_project(tmp_path)
    store = ArtifactContentStore(projects)

    with pytest.raises(
        ArtifactContentStoreConflictError,
        match="MAX_ARTIFACT_CONTENT_BYTES",
    ):
        store.put(
            handle.project_id,
            b"x" * (MAX_ARTIFACT_CONTENT_BYTES + 1),
        )


def test_same_content_across_versions_reuses_single_blob(tmp_path: Path) -> None:
    projects, handle = make_project(tmp_path)
    versions = ArtifactVersionStore(projects)
    content = b"same-version-content"

    first = versions.create_artifact(
        handle.project_id,
        input_for(content),
    )
    second = versions.append_version(
        handle.project_id,
        first.artifact_id,
        ArtifactVersionInput(
            type="mass_budget",
            producer_task="TASK-000002",
            input_dependency_versions=(),
            content=content,
            created_timestamp="2026-10-03T22:16:00Z",
        ),
    )

    assert first.hash == second.hash
    store = ArtifactContentStore(projects)
    path = store.blob_path(handle.project_id, first.hash)
    assert path.is_file()


def test_content_is_not_embedded_in_registry_json(tmp_path: Path) -> None:
    import sqlite3
    from ai.artifacts import ARTIFACT_REGISTRY_DB_FILENAME

    projects, handle = make_project(tmp_path)
    content = b"secret-binary-payload"
    record = ArtifactVersionStore(projects).create_artifact(
        handle.project_id,
        input_for(content),
    )

    registry = handle.layout.project_dir / ARTIFACT_REGISTRY_DB_FILENAME
    with sqlite3.connect(registry) as connection:
        raw = connection.execute(
            """
            SELECT record_json
            FROM artifact_versions
            WHERE artifact_id=? AND version=?
            """,
            (record.artifact_id.value, record.version),
        ).fetchone()[0]

    assert content.decode("ascii") not in raw
    assert record.hash in raw
