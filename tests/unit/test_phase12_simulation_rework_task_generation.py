from __future__ import annotations

from pathlib import Path

import pytest

import ai.simulation_feedback.rework as rework_module
from ai.agents.roles import DEFAULT_ROLE_REGISTRY, RoleRegistry
from ai.artifacts import (
    ArtifactInputVersion,
    ArtifactLifecycleStore,
    ArtifactVersionInput,
    ArtifactVersionStore,
)
from ai.contracts.artifact_states import ArtifactState
from ai.contracts.provenance import ProvenanceClass
from ai.contracts.task_states import TaskState
from ai.contracts.validation_result import ValidationCheck, ValidationResult, ValidationStatus
from ai.project.requirement_versions import RequirementVersionStore
from ai.project.requirements import RequirementSetStatus
from ai.project.store import ProjectStore
from ai.simulation_feedback import (
    MAX_SIMULATION_REWORK_TASKS,
    MISSION_VERIFIER_CHECK_IDS,
    SIMULATION_REWORK_TASK_GENERATION_CONTRACT_VERSION,
    SIMULATION_REWORK_TASK_OUTPUT_SCHEMA,
    SimulationReworkTaskGenerationConflictError,
    SimulationReworkTaskGenerator,
    classify_simulation_issues,
    invalidate_simulation_lineage,
    route_simulation_issues,
)
from ai.simulation_feedback.parser import ParsedVerifierResult
from ai.tasks.schema import MAX_TASK_ATTEMPTS, MAX_TASK_INPUT_ARTIFACTS
from ai.tasks.store import TaskStore


def _validation() -> ValidationResult:
    return ValidationResult.from_checks((
        ValidationCheck(
            check_id="deterministic",
            status=ValidationStatus.PASS,
            field=None,
            reason=None,
            details={},
        ),
    ))


def _approve(lifecycle, project_id, record):
    lifecycle.transition(project_id, record.artifact_id, record.version, ArtifactState.REVIEWED)
    lifecycle.transition(
        project_id,
        record.artifact_id,
        record.version,
        ArtifactState.VALIDATED,
        validation_result=_validation(),
    )
    lifecycle.transition(project_id, record.artifact_id, record.version, ArtifactState.APPROVED)


def _fixture(tmp_path: Path, *, requirement_count: int = 2):
    projects = ProjectStore(tmp_path / "projects")
    handle = projects.create_project()
    reqs = RequirementVersionStore(projects).create_initial(
        handle.project_id,
        [f"Requirement {index}" for index in range(requirement_count)],
        status=RequirementSetStatus.APPROVED,
    )
    versions = ArtifactVersionStore(projects)
    lifecycle = ArtifactLifecycleStore(projects)
    source = versions.create_artifact(
        handle.project_id,
        ArtifactVersionInput(
            type="mission_design",
            producer_task="TASK-000001",
            input_dependency_versions=(),
            content=b"source",
            created_timestamp="2026-10-08T01:00:00Z",
        ),
    )
    _approve(lifecycle, handle.project_id, source)
    source_ref = ArtifactInputVersion(entity_id=source.artifact_id, version=source.version)
    return projects, handle, reqs, versions, source, source_ref


def _plan(project_id, source_ref, *failed: str, runtime_run_id: str = "MISSION001-F12-5-RUN"):
    failed_set = set(failed)
    checks = {
        check_id: ("FAIL" if check_id in failed_set else "PASS")
        for check_id in MISSION_VERIFIER_CHECK_IDS
    }
    parsed = ParsedVerifierResult(
        project_id=project_id,
        requirement_version=1,
        source_artifacts=(source_ref,),
        runtime_run_id=runtime_run_id,
        mission_id="MISSION-001",
        scenario_id="F12-5-REWORK",
        overall="FAIL" if failed_set else "PASS",
        checks=checks,
        failed_checks=tuple(
            check_id for check_id in MISSION_VERIFIER_CHECK_IDS if check_id in failed_set
        ),
        failed_step_ids=("nav-1",) if failed_set else (),
        max_transition_distance_m=2.0 if "continuity" in failed_set else 0.0,
        terminal_home_distance_m=3.0 if "terminal_home" in failed_set else 0.1,
        reason="verification failed" if failed_set else None,
        verification_sha256="a" * 64,
    )
    return route_simulation_issues(classify_simulation_issues(parsed))


def _feedback(projects, plan):
    return invalidate_simulation_lineage(projects, plan)


def test_contract_and_bounds_are_explicit():
    assert SIMULATION_REWORK_TASK_GENERATION_CONTRACT_VERSION == "1.0"
    assert MAX_SIMULATION_REWORK_TASKS == 7
    assert MAX_SIMULATION_REWORK_TASKS <= 30
    assert SIMULATION_REWORK_TASK_OUTPUT_SCHEMA == "engineering_task.v1"


def test_pass_feedback_creates_no_task(tmp_path: Path):
    projects, handle, _reqs, versions, source, source_ref = _fixture(tmp_path)
    plan = _plan(handle.project_id, source_ref)
    invalidation = _feedback(projects, plan)
    result = SimulationReworkTaskGenerator(projects).generate(plan, invalidation)

    assert result.bindings == ()
    assert result.provenance is ProvenanceClass.VERIFIER_RESULT
    assert TaskStore(projects).list_tasks(handle.project_id) == ()
    assert versions.open_version(handle.project_id, source.artifact_id, 1).status is ArtifactState.APPROVED


def test_fail_feedback_creates_one_owner_task_with_requirement_context(tmp_path: Path):
    projects, handle, reqs, versions, source, source_ref = _fixture(tmp_path)
    plan = _plan(handle.project_id, source_ref, "continuity", "terminal_home")
    invalidation = _feedback(projects, plan)
    result = SimulationReworkTaskGenerator(projects).generate(plan, invalidation)

    assert len(result.bindings) == 1
    binding = result.bindings[0]
    task = binding.task
    assert binding.owner_role == "uav_systems_engineer"
    assert binding.issue_fingerprints == tuple(route.issue.fingerprint for route in plan.routes)
    assert binding.reused_existing is False
    assert task.role == "uav_systems_engineer"
    assert task.status is TaskState.CREATED
    assert task.attempt == 0
    assert task.max_attempts == MAX_TASK_ATTEMPTS
    assert task.review_required is True
    assert task.dependencies == ()
    assert task.output_schema == "engineering_task.v1"
    assert task.validators == ()
    assert tuple(ref.entity_id for ref in task.input_artifacts) == tuple(
        item.requirement_id for item in sorted(
            reqs.requirement_set.requirements,
            key=lambda item: item.requirement_id.value,
        )
    )
    assert {ref.version for ref in task.input_artifacts} == {1}
    assert source_ref not in task.input_artifacts
    assert versions.open_version(handle.project_id, source.artifact_id, 1).status is ArtifactState.STALE
    assert "checks=continuity,terminal_home" in task.title


def test_generation_is_idempotent_for_same_feedback_event(tmp_path: Path):
    projects, handle, _reqs, _versions, _source, source_ref = _fixture(tmp_path)
    plan = _plan(handle.project_id, source_ref, "continuity")
    invalidation = _feedback(projects, plan)
    generator = SimulationReworkTaskGenerator(projects)

    first = generator.generate(plan, invalidation)
    second = generator.generate(plan, invalidation)

    assert first.bindings[0].task.task_id == second.bindings[0].task.task_id
    assert first.bindings[0].reused_existing is False
    assert second.bindings[0].reused_existing is True
    assert len(TaskStore(projects).list_tasks(handle.project_id)) == 1


def test_new_runtime_event_gets_new_task_even_for_same_root_cause(tmp_path: Path):
    projects, handle, _reqs, _versions, _source, source_ref = _fixture(tmp_path)
    first_plan = _plan(handle.project_id, source_ref, "continuity", runtime_run_id="MISSION001-F12-5-RUN-A")
    first_inv = _feedback(projects, first_plan)
    generator = SimulationReworkTaskGenerator(projects)
    first = generator.generate(first_plan, first_inv)

    # F12.4 is state-idempotent, so the same stale source can be linked to the
    # next observed feedback event without rewriting artifact state.
    second_plan = _plan(handle.project_id, source_ref, "continuity", runtime_run_id="MISSION001-F12-5-RUN-B")
    second_inv = _feedback(projects, second_plan)
    second = generator.generate(second_plan, second_inv)

    assert first.bindings[0].task.task_id != second.bindings[0].task.task_id
    assert first.bindings[0].task.title != second.bindings[0].task.title
    assert len(TaskStore(projects).list_tasks(handle.project_id)) == 2


def test_requirement_context_must_remain_approved(tmp_path: Path):
    projects = ProjectStore(tmp_path / "projects")
    handle = projects.create_project()
    RequirementVersionStore(projects).create_initial(
        handle.project_id,
        ["A"],
        status=RequirementSetStatus.DRAFT,
    )
    versions = ArtifactVersionStore(projects)
    lifecycle = ArtifactLifecycleStore(projects)
    source = versions.create_artifact(
        handle.project_id,
        ArtifactVersionInput(
            type="mission_design",
            producer_task="TASK-000001",
            input_dependency_versions=(),
            content=b"source",
            created_timestamp="2026-10-08T01:00:00Z",
        ),
    )
    _approve(lifecycle, handle.project_id, source)
    ref = ArtifactInputVersion(entity_id=source.artifact_id, version=1)
    plan = _plan(handle.project_id, ref, "continuity")
    invalidation = _feedback(projects, plan)

    with pytest.raises(SimulationReworkTaskGenerationConflictError, match="APPROVED"):
        SimulationReworkTaskGenerator(projects).generate(plan, invalidation)
    assert TaskStore(projects).list_tasks(handle.project_id) == ()


def test_requirement_context_is_bounded_before_task_persistence(tmp_path: Path):
    projects, handle, _reqs, _versions, _source, source_ref = _fixture(
        tmp_path,
        requirement_count=MAX_TASK_INPUT_ARTIFACTS + 1,
    )
    plan = _plan(handle.project_id, source_ref, "continuity")
    invalidation = _feedback(projects, plan)

    with pytest.raises(SimulationReworkTaskGenerationConflictError, match="MAX_TASK_INPUT_ARTIFACTS"):
        SimulationReworkTaskGenerator(projects).generate(plan, invalidation)
    assert TaskStore(projects).list_tasks(handle.project_id) == ()


def test_invalidation_lineage_mismatch_fails_closed(tmp_path: Path):
    projects, handle, _reqs, _versions, _source, source_ref = _fixture(tmp_path)
    first_plan = _plan(handle.project_id, source_ref, "continuity", runtime_run_id="MISSION001-F12-5-A")
    invalidation = _feedback(projects, first_plan)
    other_plan = _plan(handle.project_id, source_ref, "continuity", runtime_run_id="MISSION001-F12-5-B")

    with pytest.raises(SimulationReworkTaskGenerationConflictError, match="runtime_run_id"):
        SimulationReworkTaskGenerator(projects).generate(other_plan, invalidation)
    assert TaskStore(projects).list_tasks(handle.project_id) == ()


def test_inactive_owner_registry_fails_before_task_persistence(tmp_path: Path):
    projects, handle, _reqs, _versions, _source, source_ref = _fixture(tmp_path)
    plan = _plan(handle.project_id, source_ref, "continuity")
    invalidation = _feedback(projects, plan)
    restricted = RoleRegistry((DEFAULT_ROLE_REGISTRY.get("coordinator"),))

    with pytest.raises(SimulationReworkTaskGenerationConflictError, match="not active"):
        SimulationReworkTaskGenerator(projects, restricted).generate(plan, invalidation)
    assert TaskStore(projects).list_tasks(handle.project_id) == ()


def test_conflicting_existing_identity_fails_closed(tmp_path: Path):
    projects, handle, _reqs, _versions, _source, source_ref = _fixture(tmp_path)
    plan = _plan(handle.project_id, source_ref, "continuity")
    invalidation = _feedback(projects, plan)
    generator = SimulationReworkTaskGenerator(projects)
    first = generator.generate(plan, invalidation)
    task = first.bindings[0].task

    # Simulate an impossible/corrupt recovery condition by monkeypatching the
    # expected static priority after the persisted identity already exists.
    monkeypatch_target = task.priority + 1
    original = rework_module.SIMULATION_REWORK_TASK_PRIORITY
    rework_module.SIMULATION_REWORK_TASK_PRIORITY = monkeypatch_target
    try:
        # New title is unchanged because priority is not part of the digest, so
        # the recovery path must detect contract mismatch rather than duplicate.
        with pytest.raises(SimulationReworkTaskGenerationConflictError, match="conflicting task"):
            generator.generate(plan, invalidation)
    finally:
        rework_module.SIMULATION_REWORK_TASK_PRIORITY = original


def test_result_serialization_preserves_lineage_without_issue_retry_or_human_state(tmp_path: Path):
    projects, handle, _reqs, _versions, _source, source_ref = _fixture(tmp_path)
    plan = _plan(handle.project_id, source_ref, "continuity")
    invalidation = _feedback(projects, plan)
    result = SimulationReworkTaskGenerator(projects).generate(plan, invalidation)
    raw = result.to_dict()

    assert raw["project_id"] == handle.project_id.value
    assert raw["requirement_version"] == 1
    assert raw["source_artifacts"] == [source_ref.to_dict()]
    assert raw["runtime_run_id"] == plan.runtime_run_id
    assert raw["verification_sha256"] == plan.verification_sha256
    assert raw["provenance"] == "VERIFIER_RESULT"
    encoded = repr(raw)
    assert "issue_id" not in encoded
    assert "retry_attempt" not in encoded
    assert "needs_human" not in encoded


def test_wrong_inputs_fail_closed(tmp_path: Path):
    projects, handle, _reqs, _versions, _source, source_ref = _fixture(tmp_path)
    plan = _plan(handle.project_id, source_ref, "continuity")
    invalidation = _feedback(projects, plan)
    generator = SimulationReworkTaskGenerator(projects)
    with pytest.raises(TypeError, match="SimulationIssueRolePlan"):
        generator.generate(object(), invalidation)  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="SimulationLineageInvalidationResult"):
        generator.generate(plan, object())  # type: ignore[arg-type]
