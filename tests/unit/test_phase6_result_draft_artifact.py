from __future__ import annotations

import json
from pathlib import Path
import sqlite3

import pytest

from ai.agents import (
    AGENT_RESULT_OUTPUT_SCHEMA_ID,
    MAX_RESULT_ARTIFACT_RECOVERY_CANDIDATES,
    MAX_RESULT_DRAFT_ARTIFACT_BYTES,
    RESULT_DRAFT_ARTIFACT_CONTRACT_VERSION,
    RESULT_DRAFT_ARTIFACT_LOCK_FILENAME,
    RESULT_DRAFT_ARTIFACT_TYPE,
    AgentRunStore,
    ContextManifestStore,
    MinimalContextBuilder,
    ResultDraftArtifactConflictError,
    ResultDraftArtifactPayload,
    ResultDraftArtifactStore,
    SchemaEnforcedOutputValidator,
)
from ai.artifacts import (
    ARTIFACT_REGISTRY_DB_FILENAME,
    ArtifactContentStore,
    ArtifactInputVersion,
    ArtifactLifecycleStore,
    ArtifactVersionStore,
)
from ai.contracts.artifact_states import ArtifactState
from ai.project.requirement_versions import RequirementVersionStore
from ai.project.requirements import RequirementSetStatus
from ai.project.store import ProjectStore
from ai.providers.contracts import GenerateResponse
from ai.tasks import TaskCreateInput, TaskStore


CREATED_AT = "2026-10-05T01:30:00Z"


def task_input(*, inputs=()):
    return TaskCreateInput(
        title="Persist agent result as DRAFT artifact",
        role="uav_systems_engineer",
        priority=100,
        dependencies=(),
        input_artifacts=tuple(inputs),
        output_schema=AGENT_RESULT_OUTPUT_SCHEMA_ID,
        validators=("schema",),
        review_required=False,
        max_attempts=3,
    )


def response(*, nested_state="APPROVED"):
    payload = {
        "status": "PROPOSED",
        "claims": [
            {
                "classification": "AI_PROPOSAL",
                "statement": "Use the bounded draft proposal.",
            }
        ],
        "assumptions": [],
        "calculations": [{"mass_kg": 2.1}],
        "artifacts": [
            {
                "kind": "design_note",
                "state": nested_state,
                "note": "Nested model content is not lifecycle authority.",
            }
        ],
        "risks": [],
        "missing_information": [],
        "open_questions": [],
    }
    return GenerateResponse(
        model="local-test",
        content=json.dumps(payload, separators=(",", ":")),
        done=True,
    )


def setup(tmp_path: Path, *, with_requirement=False):
    projects = ProjectStore(tmp_path / "remote-uav" / "projects")
    handle = projects.create_project()
    inputs = ()
    requirement_id = None
    if with_requirement:
        version = RequirementVersionStore(projects).create_initial(
            handle.project_id,
            ["Payload shall remain below 2 kg."],
            status=RequirementSetStatus.APPROVED,
        )
        requirement_id = version.requirement_set.requirements[0].requirement_id
        inputs = (ArtifactInputVersion(requirement_id, version.version),)
    task = TaskStore(projects).create_task(
        handle.project_id, task_input(inputs=inputs)
    )
    context = MinimalContextBuilder(projects).build(
        handle.project_id, task.task_id
    )
    output = SchemaEnforcedOutputValidator(projects).enforce(
        handle.project_id, task.task_id, response()
    )
    run = AgentRunStore(projects).create_run(output)
    manifest = ContextManifestStore(projects).create_manifest(run, context)
    return projects, handle, task, run, manifest, requirement_id


def test_contract_constants_are_explicit_and_bounded():
    assert RESULT_DRAFT_ARTIFACT_CONTRACT_VERSION == "1.0"
    assert RESULT_DRAFT_ARTIFACT_TYPE == "ai.agent_result.v1"
    assert MAX_RESULT_DRAFT_ARTIFACT_BYTES == 10 * 1024 * 1024
    assert MAX_RESULT_ARTIFACT_RECOVERY_CANDIDATES == 4096
    assert RESULT_DRAFT_ARTIFACT_LOCK_FILENAME == ".result-draft-artifact.lock"


def test_create_result_artifact_is_draft(tmp_path):
    projects, handle, task, run, manifest, _ = setup(tmp_path)
    created = ResultDraftArtifactStore(projects).create_artifact(
        handle.project_id, run.run_id, created_timestamp=CREATED_AT
    )
    assert created.record.artifact_id.value == "ART-000001"
    assert created.record.version == 1
    assert created.record.status is ArtifactState.DRAFT
    assert created.record.validation_result is None
    assert created.record.type == RESULT_DRAFT_ARTIFACT_TYPE
    assert created.record.producer_task == task.task_id
    assert created.payload.run_id == run.run_id
    assert created.payload.context_manifest_sha256 == manifest.manifest_sha256


def test_canonical_artifact_id_is_allocated_by_phase4_store(tmp_path):
    projects, handle, _task, run, _manifest, _ = setup(tmp_path)
    store = ResultDraftArtifactStore(projects)
    first = store.create_artifact(
        handle.project_id, run.run_id, created_timestamp=CREATED_AT
    )
    # Same run is idempotently recovered, not allocated again.
    second = store.create_artifact(
        handle.project_id, run.run_id, created_timestamp="2026-10-05T01:31:00Z"
    )
    assert first == second
    assert first.record.artifact_id.value == "ART-000001"


def test_content_binds_run_schema_context_and_result(tmp_path):
    projects, handle, _task, run, manifest, _ = setup(tmp_path)
    created = ResultDraftArtifactStore(projects).create_artifact(
        handle.project_id, run.run_id, created_timestamp=CREATED_AT
    )
    payload = created.payload.to_dict()
    assert payload["contract_version"] == "1.0"
    assert payload["run_id"] == run.run_id.value
    assert payload["project_id"] == handle.project_id.value
    assert payload["task_id"] == run.task_id.value
    assert payload["schema_id"] == "engineering_task.v1"
    assert payload["context_manifest_sha256"] == manifest.manifest_sha256
    assert payload["context_sha256"] == manifest.context_sha256
    assert payload["result"]["status"] == "PROPOSED"


def test_task_input_dependencies_become_artifact_lineage(tmp_path):
    projects, handle, task, run, _manifest, requirement_id = setup(
        tmp_path, with_requirement=True
    )
    created = ResultDraftArtifactStore(projects).create_artifact(
        handle.project_id, run.run_id, created_timestamp=CREATED_AT
    )
    assert created.record.input_dependency_versions == task.input_artifacts
    assert created.record.input_dependency_versions == (
        ArtifactInputVersion(requirement_id, 1),
    )


def test_nested_model_artifact_state_never_promotes_record(tmp_path):
    projects, handle, _task, run, _manifest, _ = setup(tmp_path)
    created = ResultDraftArtifactStore(projects).create_artifact(
        handle.project_id, run.run_id, created_timestamp=CREATED_AT
    )
    assert created.payload.result.to_dict()["artifacts"][0]["state"] == "APPROVED"
    assert created.record.status is ArtifactState.DRAFT
    assert created.record.validation_result is None


def test_restart_reopens_verified_result_artifact(tmp_path):
    projects, handle, _task, run, _manifest, _ = setup(tmp_path)
    first = ResultDraftArtifactStore(projects).create_artifact(
        handle.project_id, run.run_id, created_timestamp=CREATED_AT
    )
    restarted = ResultDraftArtifactStore(ProjectStore(projects.projects_root))
    second = restarted.open_artifact(
        handle.project_id, first.record.artifact_id, first.record.version
    )
    assert second == first


def test_artifact_content_is_canonical_json(tmp_path):
    projects, handle, _task, run, _manifest, _ = setup(tmp_path)
    created = ResultDraftArtifactStore(projects).create_artifact(
        handle.project_id, run.run_id, created_timestamp=CREATED_AT
    )
    raw = ArtifactContentStore(projects).read(
        handle.project_id, created.record.hash
    )
    assert raw == created.payload.canonical_bytes()
    assert raw.endswith(b"\n")


def test_missing_manifest_fails_before_artifact_creation(tmp_path):
    projects = ProjectStore(tmp_path / "remote-uav" / "projects")
    handle = projects.create_project()
    task = TaskStore(projects).create_task(handle.project_id, task_input())
    output = SchemaEnforcedOutputValidator(projects).enforce(
        handle.project_id, task.task_id, response()
    )
    run = AgentRunStore(projects).create_run(output)
    with pytest.raises(ResultDraftArtifactConflictError, match="inputs"):
        ResultDraftArtifactStore(projects).create_artifact(
            handle.project_id, run.run_id, created_timestamp=CREATED_AT
        )
    registry = handle.layout.project_dir / ARTIFACT_REGISTRY_DB_FILENAME
    assert not registry.exists()


def test_context_drift_after_manifest_fails_before_artifact(tmp_path):
    from ai.contracts.provenance import ProvenanceClass
    from ai.project.facts import Fact, FactSet, FactStore

    projects = ProjectStore(tmp_path / "remote-uav" / "projects")
    handle = projects.create_project()
    facts = FactStore(projects)
    facts.save(
        FactSet(
            project_id=handle.project_id,
            facts=(Fact("vehicle.mass_kg", 2.1, ProvenanceClass.USER_FACT),),
        )
    )
    task = TaskStore(projects).create_task(handle.project_id, task_input())
    context = MinimalContextBuilder(projects).build(
        handle.project_id, task.task_id, fact_keys=("vehicle.mass_kg",)
    )
    output = SchemaEnforcedOutputValidator(projects).enforce(
        handle.project_id, task.task_id, response()
    )
    run = AgentRunStore(projects).create_run(output)
    ContextManifestStore(projects).create_manifest(run, context)
    facts.save(
        FactSet(
            project_id=handle.project_id,
            facts=(Fact("vehicle.mass_kg", 2.2, ProvenanceClass.USER_FACT),),
        )
    )
    with pytest.raises(ResultDraftArtifactConflictError, match="inputs"):
        ResultDraftArtifactStore(projects).create_artifact(
            handle.project_id, run.run_id, created_timestamp=CREATED_AT
        )


def test_tampered_artifact_blob_fails_closed(tmp_path):
    projects, handle, _task, run, _manifest, _ = setup(tmp_path)
    store = ResultDraftArtifactStore(projects)
    created = store.create_artifact(
        handle.project_id, run.run_id, created_timestamp=CREATED_AT
    )
    content = ArtifactContentStore(projects)
    path = content.blob_path(handle.project_id, created.record.hash)
    path.write_bytes(b"tampered")
    with pytest.raises(ResultDraftArtifactConflictError, match="integrity"):
        store.open_artifact(
            handle.project_id, created.record.artifact_id, created.record.version
        )


def test_payload_parser_rejects_extra_fields(tmp_path):
    projects, handle, _task, run, _manifest, _ = setup(tmp_path)
    created = ResultDraftArtifactStore(projects).create_artifact(
        handle.project_id, run.run_id, created_timestamp=CREATED_AT
    )
    with pytest.raises(ResultDraftArtifactConflictError, match="exactly"):
        ResultDraftArtifactPayload.from_dict(
            {**created.payload.to_dict(), "approval": True}
        )


def test_payload_parser_rejects_non_proposed_result(tmp_path):
    projects, handle, _task, run, _manifest, _ = setup(tmp_path)
    created = ResultDraftArtifactStore(projects).create_artifact(
        handle.project_id, run.run_id, created_timestamp=CREATED_AT
    )
    raw = created.payload.to_dict()
    raw["result"] = {**raw["result"], "status": "PASS"}
    with pytest.raises(ResultDraftArtifactConflictError, match="AgentResult"):
        ResultDraftArtifactPayload.from_dict(raw)


def test_task_state_and_attempt_are_unchanged(tmp_path):
    projects, handle, task, run, _manifest, _ = setup(tmp_path)
    tasks = TaskStore(projects)
    before = tasks.open_task(handle.project_id, task.task_id)
    ResultDraftArtifactStore(projects).create_artifact(
        handle.project_id, run.run_id, created_timestamp=CREATED_AT
    )
    assert tasks.open_task(handle.project_id, task.task_id) == before


def test_run_and_manifest_are_unchanged(tmp_path):
    projects, handle, _task, run, manifest, _ = setup(tmp_path)
    runs = AgentRunStore(projects)
    manifests = ContextManifestStore(projects)
    before_run = runs.open_run(handle.project_id, run.run_id)
    before_manifest = manifests.open_manifest(handle.project_id, run.run_id)
    ResultDraftArtifactStore(projects).create_artifact(
        handle.project_id, run.run_id, created_timestamp=CREATED_AT
    )
    assert runs.open_run(handle.project_id, run.run_id) == before_run
    assert manifests.open_manifest(handle.project_id, run.run_id) == before_manifest == manifest


def test_g66_registry_stays_exactly_two_tables(tmp_path):
    projects, handle, _task, run, _manifest, _ = setup(tmp_path)
    ResultDraftArtifactStore(projects).create_artifact(
        handle.project_id, run.run_id, created_timestamp=CREATED_AT
    )
    path = handle.layout.data_directories["ai_runs"] / "agent_run_registry.sqlite3"
    with sqlite3.connect(path) as connection:
        tables = tuple(
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )
        )
    assert tables == ("agent_run_registry_meta", "agent_runs")


def test_phase4_registry_stays_exactly_six_tables(tmp_path):
    projects, handle, _task, run, _manifest, _ = setup(tmp_path)
    ResultDraftArtifactStore(projects).create_artifact(
        handle.project_id, run.run_id, created_timestamp=CREATED_AT
    )
    path = handle.layout.project_dir / ARTIFACT_REGISTRY_DB_FILENAME
    with sqlite3.connect(path) as connection:
        tables = tuple(
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )
        )
    assert tables == (
        "artifact_dependency_edges",
        "artifact_heads",
        "artifact_lifecycle",
        "artifact_registry_meta",
        "artifact_supersession",
        "artifact_versions",
    )


def test_existing_artifact_promoted_after_creation_blocks_duplicate(tmp_path):
    projects, handle, _task, run, _manifest, _ = setup(tmp_path)
    store = ResultDraftArtifactStore(projects)
    created = store.create_artifact(
        handle.project_id, run.run_id, created_timestamp=CREATED_AT
    )
    ArtifactLifecycleStore(projects).transition(
        handle.project_id,
        created.record.artifact_id,
        created.record.version,
        ArtifactState.REVIEWED,
    )
    with pytest.raises(ResultDraftArtifactConflictError, match="no longer DRAFT"):
        store.create_artifact(
            handle.project_id,
            run.run_id,
            created_timestamp="2026-10-05T01:31:00Z",
        )


def test_bad_timestamp_does_not_consume_artifact_sequence(tmp_path):
    projects, handle, _task, run, _manifest, _ = setup(tmp_path)
    store = ResultDraftArtifactStore(projects)
    with pytest.raises(ResultDraftArtifactConflictError, match="Phase-4"):
        store.create_artifact(
            handle.project_id,
            run.run_id,
            created_timestamp="not-a-timestamp",
        )
    created = store.create_artifact(
        handle.project_id, run.run_id, created_timestamp=CREATED_AT
    )
    assert created.record.artifact_id.value == "ART-000001"


def test_store_exposes_no_review_validation_approval_provider_or_uav_api(tmp_path):
    projects, _handle, _task, _run, _manifest, _ = setup(tmp_path)
    store = ResultDraftArtifactStore(projects)
    for name in (
        "generate",
        "execute",
        "provider",
        "retry",
        "review",
        "validate",
        "approve",
        "integrate",
        "command_uav",
    ):
        assert not hasattr(store, name)


def test_module_has_no_provider_uav_or_retry_loop_imports():
    import ai.agents.draft_artifact as module

    text = Path(module.__file__).read_text(encoding="utf-8").lower()
    assert "while true" not in text
    assert "time.sleep" not in text
    for forbidden in ("mavsdk", "px4", "gazebo", "ollama", "mockprovider"):
        assert forbidden not in text
