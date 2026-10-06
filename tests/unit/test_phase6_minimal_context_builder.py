from __future__ import annotations

from pathlib import Path

import pytest

from ai.agents import (
    CONTEXT_POLICY_HIERARCHY,
    MAX_CONTEXT_ARTIFACT_TEXT_BYTES,
    MAX_CONTEXT_FACT_KEYS,
    MAX_MINIMAL_CONTEXT_BYTES,
    MINIMAL_CONTEXT_CONTRACT_VERSION,
    MinimalContextBuilder,
    MinimalContextConflictError,
    MinimalContextLimitError,
)
from ai.artifacts import (
    ArtifactInputVersion,
    ArtifactLifecycleStore,
    ArtifactVersionInput,
    ArtifactVersionStore,
)
from ai.contracts.artifact_states import ArtifactState
from ai.contracts.provenance import ProvenanceClass
from ai.contracts.validation_result import (
    ValidationCheck,
    ValidationResult,
    ValidationStatus,
)
from ai.project.facts import Fact, FactSet, FactStore
from ai.project.requirement_versions import RequirementVersionStore
from ai.project.requirements import RequirementSetStatus
from ai.project.store import ProjectStore
from ai.tasks import TaskCreateInput, TaskStore


def pass_validation() -> ValidationResult:
    return ValidationResult.from_checks((ValidationCheck(
        check_id="g6-context-parent",
        status=ValidationStatus.PASS,
        field=None,
        reason=None,
        details={},
    ),))


def approve_artifact(projects, project_id, record) -> None:
    lifecycle = ArtifactLifecycleStore(projects)
    lifecycle.transition(project_id, record.artifact_id, record.version, ArtifactState.REVIEWED)
    lifecycle.transition(
        project_id, record.artifact_id, record.version, ArtifactState.VALIDATED,
        validation_result=pass_validation(),
    )
    lifecycle.transition(project_id, record.artifact_id, record.version, ArtifactState.APPROVED)


def make_project(tmp_path: Path):
    projects = ProjectStore(tmp_path / "remote-uav" / "projects")
    handle = projects.create_project()
    return projects, handle


def create_task(projects, handle, *, inputs=(), role="uav_systems_engineer"):
    return TaskStore(projects).create_task(
        handle.project_id,
        TaskCreateInput(
            title="Build bounded UAV engineering context",
            role=role,
            priority=100,
            dependencies=(),
            input_artifacts=tuple(inputs),
            output_schema="engineering_task.v1",
            validators=("schema",),
            review_required=False,
            max_attempts=3,
        ),
    )


def test_contract_and_bounds_are_explicit() -> None:
    assert MINIMAL_CONTEXT_CONTRACT_VERSION == "1.0"
    assert MAX_CONTEXT_FACT_KEYS == 64
    assert MAX_CONTEXT_ARTIFACT_TEXT_BYTES == 256 * 1024
    assert MAX_MINIMAL_CONTEXT_BYTES == 1024 * 1024
    assert CONTEXT_POLICY_HIERARCHY == (
        "SYSTEM_POLICY",
        "ROLE_POLICY",
        "TASK_CONTRACT",
        "APPROVED_PROJECT_STATE",
        "RETRIEVED_DOCUMENT_CONTENT",
    )


def test_minimal_context_contains_role_policy_and_task_contract(tmp_path: Path) -> None:
    projects, handle = make_project(tmp_path)
    task = create_task(projects, handle)
    context = MinimalContextBuilder(projects).build(handle.project_id, task.task_id)
    assert context.binding.task_id == task.task_id
    assert context.role_policy.role_id == "uav_systems_engineer"
    assert context.task_contract == task
    assert context.approved_requirements == ()
    assert context.approved_parent_artifacts == ()
    assert context.project_facts == ()
    assert context.encoded_size_bytes > 0


def test_context_dict_structurally_separates_policy_and_project_data(tmp_path: Path) -> None:
    projects, handle = make_project(tmp_path)
    task = create_task(projects, handle)
    payload = MinimalContextBuilder(projects).build(handle.project_id, task.task_id).to_dict()
    assert payload["role_policy"]["role_id"] == "uav_systems_engineer"
    assert payload["task_contract"]["task_id"] == task.task_id.value
    assert payload["reserved_context_sections"] == {
        "previous_review_issues": [],
        "previous_validation_failures": [],
        "retrieved_documents": [],
    }


def test_explicit_approved_requirement_reference_is_selected(tmp_path: Path) -> None:
    projects, handle = make_project(tmp_path)
    version = RequirementVersionStore(projects).create_initial(
        handle.project_id,
        ["Payload shall remain below 2 kg."],
        status=RequirementSetStatus.APPROVED,
    )
    req = version.requirement_set.requirements[0]
    task = create_task(projects, handle, inputs=(ArtifactInputVersion(req.requirement_id, 1),))
    context = MinimalContextBuilder(projects).build(handle.project_id, task.task_id)
    assert [(r.requirement_id, r.version, r.text) for r in context.approved_requirements] == [
        (req.requirement_id.value, 1, "Payload shall remain below 2 kg.")
    ]


def test_draft_requirement_reference_fails_closed(tmp_path: Path) -> None:
    projects, handle = make_project(tmp_path)
    version = RequirementVersionStore(projects).create_initial(handle.project_id, ["Draft requirement"])
    req = version.requirement_set.requirements[0]
    task = create_task(projects, handle, inputs=(ArtifactInputVersion(req.requirement_id, 1),))
    with pytest.raises(MinimalContextConflictError, match="not APPROVED"):
        MinimalContextBuilder(projects).build(handle.project_id, task.task_id)


def test_requirement_id_absent_from_referenced_version_fails_closed(tmp_path: Path) -> None:
    projects, handle = make_project(tmp_path)
    store = RequirementVersionStore(projects)
    first = store.create_initial(handle.project_id, ["A"], status=RequirementSetStatus.APPROVED)
    old_id = first.requirement_set.requirements[0].requirement_id
    second = store.append_version(handle.project_id, ["B"], status=RequirementSetStatus.APPROVED)
    task = create_task(projects, handle, inputs=(ArtifactInputVersion(old_id, second.version),))
    with pytest.raises(MinimalContextConflictError, match="absent"):
        MinimalContextBuilder(projects).build(handle.project_id, task.task_id)


def test_explicit_approved_parent_artifact_text_is_selected(tmp_path: Path) -> None:
    projects, handle = make_project(tmp_path)
    producer = create_task(projects, handle)
    versions = ArtifactVersionStore(projects)
    artifact = versions.create_artifact(
        handle.project_id,
        ArtifactVersionInput(
            type="uav.design_note",
            producer_task=producer.task_id,
            input_dependency_versions=(),
            content=b'{"mass_kg":2.1}',
            created_timestamp="2026-10-04T12:00:00Z",
        ),
    )
    approve_artifact(projects, handle.project_id, artifact)
    consumer = create_task(projects, handle, inputs=(ArtifactInputVersion(artifact.artifact_id, 1),))
    context = MinimalContextBuilder(projects).build(handle.project_id, consumer.task_id)
    assert len(context.approved_parent_artifacts) == 1
    selected = context.approved_parent_artifacts[0]
    assert selected.artifact_id == artifact.artifact_id.value
    assert selected.version == 1
    assert selected.content_text == '{"mass_kg":2.1}'


def test_nonapproved_parent_artifact_fails_closed(tmp_path: Path) -> None:
    projects, handle = make_project(tmp_path)
    producer = create_task(projects, handle)
    artifact = ArtifactVersionStore(projects).create_artifact(
        handle.project_id,
        ArtifactVersionInput(
            type="uav.design_note", producer_task=producer.task_id,
            input_dependency_versions=(), content=b"draft",
            created_timestamp="2026-10-04T12:00:00Z",
        ),
    )
    consumer = create_task(projects, handle, inputs=(ArtifactInputVersion(artifact.artifact_id, 1),))
    with pytest.raises(MinimalContextConflictError, match="not APPROVED"):
        MinimalContextBuilder(projects).build(handle.project_id, consumer.task_id)


def test_binary_parent_artifact_is_rejected_from_minimal_text_context(tmp_path: Path) -> None:
    projects, handle = make_project(tmp_path)
    producer = create_task(projects, handle)
    artifact = ArtifactVersionStore(projects).create_artifact(
        handle.project_id,
        ArtifactVersionInput(
            type="uav.binary", producer_task=producer.task_id,
            input_dependency_versions=(), content=b"\xff\xfe\x80",
            created_timestamp="2026-10-04T12:00:00Z",
        ),
    )
    approve_artifact(projects, handle.project_id, artifact)
    consumer = create_task(projects, handle, inputs=(ArtifactInputVersion(artifact.artifact_id, 1),))
    with pytest.raises(MinimalContextConflictError, match="UTF-8"):
        MinimalContextBuilder(projects).build(handle.project_id, consumer.task_id)


def test_explicit_fact_keys_select_only_requested_facts_in_canonical_order(tmp_path: Path) -> None:
    projects, handle = make_project(tmp_path)
    FactStore(projects).save(FactSet(
        project_id=handle.project_id,
        facts=(
            Fact("vehicle.mass_kg", 2.1, ProvenanceClass.USER_FACT),
            Fact("mission.endurance_min", 20, ProvenanceClass.VERIFIED_FACT),
            Fact("vehicle.name", "Scout", ProvenanceClass.USER_FACT),
        ),
    ))
    task = create_task(projects, handle)
    context = MinimalContextBuilder(projects).build(
        handle.project_id, task.task_id,
        fact_keys=("vehicle.mass_kg", "mission.endurance_min"),
    )
    assert [f.key for f in context.project_facts] == [
        "mission.endurance_min", "vehicle.mass_kg"
    ]
    assert all(f.key != "vehicle.name" for f in context.project_facts)


def test_missing_requested_fact_fails_closed(tmp_path: Path) -> None:
    projects, handle = make_project(tmp_path)
    FactStore(projects).save(FactSet(
        project_id=handle.project_id,
        facts=(Fact("vehicle.mass_kg", 2.1, ProvenanceClass.USER_FACT),),
    ))
    task = create_task(projects, handle)
    with pytest.raises(MinimalContextConflictError, match="missing"):
        MinimalContextBuilder(projects).build(
            handle.project_id, task.task_id, fact_keys=("vehicle.range_km",)
        )


def test_requesting_facts_without_fact_store_fails_closed(tmp_path: Path) -> None:
    projects, handle = make_project(tmp_path)
    task = create_task(projects, handle)
    with pytest.raises(MinimalContextConflictError, match="cannot be opened"):
        MinimalContextBuilder(projects).build(
            handle.project_id, task.task_id, fact_keys=("vehicle.mass_kg",)
        )


def test_duplicate_fact_keys_are_rejected(tmp_path: Path) -> None:
    projects, handle = make_project(tmp_path)
    task = create_task(projects, handle)
    with pytest.raises(MinimalContextConflictError, match="duplicates"):
        MinimalContextBuilder(projects).build(
            handle.project_id, task.task_id,
            fact_keys=("vehicle.mass_kg", "vehicle.mass_kg"),
        )


def test_fact_key_selection_is_bounded(tmp_path: Path) -> None:
    projects, handle = make_project(tmp_path)
    task = create_task(projects, handle)
    with pytest.raises(MinimalContextLimitError, match="MAX_CONTEXT_FACT_KEYS"):
        MinimalContextBuilder(projects).build(
            handle.project_id, task.task_id,
            fact_keys=tuple(f"fact.{i}" for i in range(MAX_CONTEXT_FACT_KEYS + 1)),
        )


def test_unknown_role_mapping_failure_propagates_as_context_conflict(tmp_path: Path) -> None:
    projects, handle = make_project(tmp_path)
    task = create_task(projects, handle, role="invented_engineer")
    with pytest.raises(MinimalContextConflictError, match="mapping failed"):
        MinimalContextBuilder(projects).build(handle.project_id, task.task_id)


def test_context_build_does_not_mutate_task(tmp_path: Path) -> None:
    projects, handle = make_project(tmp_path)
    tasks = TaskStore(projects)
    task = create_task(projects, handle)
    before = tasks.open_task(handle.project_id, task.task_id)
    MinimalContextBuilder(projects).build(handle.project_id, task.task_id)
    after = tasks.open_task(handle.project_id, task.task_id)
    assert after == before


def test_context_build_is_repeatable(tmp_path: Path) -> None:
    projects, handle = make_project(tmp_path)
    task = create_task(projects, handle)
    builder = MinimalContextBuilder(projects)
    first = builder.build(handle.project_id, task.task_id)
    second = builder.build(handle.project_id, task.task_id)
    assert first == second
    assert first.to_dict() == second.to_dict()


def test_context_survives_restart(tmp_path: Path) -> None:
    projects, handle = make_project(tmp_path)
    task = create_task(projects, handle)
    first = MinimalContextBuilder(projects).build(handle.project_id, task.task_id)
    second = MinimalContextBuilder(ProjectStore(projects.projects_root)).build(
        handle.project_id, task.task_id
    )
    assert second == first


def test_builder_exposes_no_provider_execution_or_prompt_api(tmp_path: Path) -> None:
    projects, _handle = make_project(tmp_path)
    builder = MinimalContextBuilder(projects)
    for name in (
        "generate", "run", "execute", "invoke_model", "provider", "model_manager",
        "render_prompt", "system_prompt", "persist_run", "create_artifact",
        "approve", "command_uav",
    ):
        assert not hasattr(builder, name)
