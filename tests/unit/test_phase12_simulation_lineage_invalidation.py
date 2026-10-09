from __future__ import annotations

from pathlib import Path

import pytest

from ai.artifacts import (
    ArtifactInputVersion,
    ArtifactLifecycleStore,
    ArtifactVersionInput,
    ArtifactVersionStore,
)
from ai.contracts.artifact_states import ArtifactState
from ai.contracts.ids import CanonicalId, IdKind
from ai.contracts.provenance import ProvenanceClass
from ai.contracts.validation_result import ValidationCheck, ValidationResult, ValidationStatus
from ai.project.store import ProjectStore
from ai.simulation_feedback.invalidation import (
    MAX_SIMULATION_INVALIDATION_ROOTS,
    SIMULATION_LINEAGE_INVALIDATION_CONTRACT_VERSION,
    SimulationLineageInvalidationError,
    invalidate_simulation_lineage,
)
from ai.simulation_feedback.parser import MISSION_VERIFIER_CHECK_IDS, ParsedVerifierResult
from ai.simulation_feedback.routing import route_simulation_issues
from ai.simulation_feedback.taxonomy import classify_simulation_issues


def _project(tmp_path: Path):
    projects = ProjectStore(tmp_path / "projects")
    handle = projects.create_project()
    return projects, handle


def _artifact_input(kind: str, task: str, body: bytes, timestamp: str, deps=()):
    return ArtifactVersionInput(
        type=kind,
        producer_task=task,
        input_dependency_versions=tuple(deps),
        content=body,
        created_timestamp=timestamp,
    )


def _validation() -> ValidationResult:
    return ValidationResult.from_checks(
        (
            ValidationCheck(
                check_id="deterministic",
                status=ValidationStatus.PASS,
                field=None,
                reason=None,
                details={},
            ),
        )
    )


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


def _plan(project_id, source_artifacts, *failed: str):
    failed_set = set(failed)
    checks = {
        check_id: ("FAIL" if check_id in failed_set else "PASS")
        for check_id in MISSION_VERIFIER_CHECK_IDS
    }
    parsed = ParsedVerifierResult(
        project_id=project_id,
        requirement_version=7,
        source_artifacts=tuple(source_artifacts),
        runtime_run_id="MISSION001-F12-4-RUN",
        mission_id="MISSION-001",
        scenario_id="F12-4-LINEAGE",
        overall="FAIL" if failed_set else "PASS",
        checks=checks,
        failed_checks=tuple(
            check_id for check_id in MISSION_VERIFIER_CHECK_IDS if check_id in failed_set
        ),
        failed_step_ids=("nav-1",) if failed_set else (),
        max_transition_distance_m=2.0 if "continuity" in failed_set else 0.0,
        terminal_home_distance_m=3.0 if "terminal_home" in failed_set else 0.1,
        reason="verification failed" if failed_set else None,
        verification_sha256="e" * 64,
    )
    return route_simulation_issues(classify_simulation_issues(parsed))


def _chain(tmp_path: Path):
    projects, handle = _project(tmp_path)
    versions = ArtifactVersionStore(projects)
    lifecycle = ArtifactLifecycleStore(projects)

    source = versions.create_artifact(
        handle.project_id,
        _artifact_input(
            "mission_design",
            "TASK-000001",
            b"source",
            "2026-10-07T01:00:00Z",
        ),
    )
    child = versions.create_artifact(
        handle.project_id,
        _artifact_input(
            "derived_analysis",
            "TASK-000002",
            b"child",
            "2026-10-07T01:01:00Z",
            (
                ArtifactInputVersion(
                    entity_id=source.artifact_id,
                    version=source.version,
                ),
            ),
        ),
    )
    unrelated = versions.create_artifact(
        handle.project_id,
        _artifact_input(
            "unrelated",
            "TASK-000003",
            b"unrelated",
            "2026-10-07T01:02:00Z",
        ),
    )
    for record in (source, child, unrelated):
        _approve(lifecycle, handle.project_id, record)
    return projects, handle, versions, source, child, unrelated


def _ref(record):
    return ArtifactInputVersion(entity_id=record.artifact_id, version=record.version)


def test_contract_and_bound_are_explicit():
    assert SIMULATION_LINEAGE_INVALIDATION_CONTRACT_VERSION == "1.0"
    assert MAX_SIMULATION_INVALIDATION_ROOTS == 64


def test_pass_plan_does_not_mutate_source_lineage(tmp_path: Path):
    projects, handle, versions, source, child, unrelated = _chain(tmp_path)
    result = invalidate_simulation_lineage(
        projects,
        _plan(handle.project_id, (_ref(source),)),
    )

    assert result.invalidation_roots == ()
    assert result.affected == ()
    assert result.changed_to_stale == ()
    assert result.already_historical == ()
    assert result.issue_fingerprints == ()
    assert versions.open_version(handle.project_id, source.artifact_id, 1).status is ArtifactState.APPROVED
    assert versions.open_version(handle.project_id, child.artifact_id, 1).status is ArtifactState.APPROVED
    assert versions.open_version(handle.project_id, unrelated.artifact_id, 1).status is ArtifactState.APPROVED


def test_fail_invalidates_exact_source_and_transitive_descendants(tmp_path: Path):
    projects, handle, versions, source, child, unrelated = _chain(tmp_path)
    plan = _plan(handle.project_id, (_ref(source),), "continuity")
    result = invalidate_simulation_lineage(projects, plan)

    assert result.invalidation_roots == (_ref(source),)
    assert len(result.issue_fingerprints) == 1
    assert tuple((ref.artifact_id, ref.version) for ref in result.changed_to_stale) == (
        (source.artifact_id, 1),
        (child.artifact_id, 1),
    )
    assert versions.open_version(handle.project_id, source.artifact_id, 1).status is ArtifactState.STALE
    assert versions.open_version(handle.project_id, child.artifact_id, 1).status is ArtifactState.STALE
    assert versions.open_version(handle.project_id, unrelated.artifact_id, 1).status is ArtifactState.APPROVED
    assert result.provenance is ProvenanceClass.VERIFIER_RESULT


def test_multiple_source_roots_are_all_invalidated(tmp_path: Path):
    projects, handle = _project(tmp_path)
    versions = ArtifactVersionStore(projects)
    lifecycle = ArtifactLifecycleStore(projects)
    first = versions.create_artifact(
        handle.project_id,
        _artifact_input("first", "TASK-000001", b"a", "2026-10-07T01:00:00Z"),
    )
    second = versions.create_artifact(
        handle.project_id,
        _artifact_input("second", "TASK-000002", b"b", "2026-10-07T01:01:00Z"),
    )
    for record in (first, second):
        _approve(lifecycle, handle.project_id, record)

    result = invalidate_simulation_lineage(
        projects,
        _plan(handle.project_id, (_ref(second), _ref(first)), "terminal_home"),
    )
    assert result.invalidation_roots == tuple(
        sorted((_ref(first), _ref(second)), key=lambda item: (item.entity_id.value, item.version))
    )
    assert len(result.changed_to_stale) == 2


def test_second_invalidation_is_state_idempotent(tmp_path: Path):
    projects, handle, _versions, source, _child, _unrelated = _chain(tmp_path)
    plan = _plan(handle.project_id, (_ref(source),), "terminal_landed_state")

    first = invalidate_simulation_lineage(projects, plan)
    second = invalidate_simulation_lineage(projects, plan)

    assert len(first.changed_to_stale) == 2
    assert second.changed_to_stale == ()
    assert len(second.already_historical) == 2


def test_all_roots_are_prevalidated_before_any_mutation(tmp_path: Path):
    projects, handle, versions, source, child, _unrelated = _chain(tmp_path)
    missing = ArtifactInputVersion(
        entity_id=CanonicalId.from_sequence(IdKind.ARTIFACT, 999999),
        version=1,
    )
    plan = _plan(handle.project_id, (_ref(source), missing), "continuity")

    with pytest.raises(SimulationLineageInvalidationError, match="unavailable"):
        invalidate_simulation_lineage(projects, plan)

    assert versions.open_version(handle.project_id, source.artifact_id, 1).status is ArtifactState.APPROVED
    assert versions.open_version(handle.project_id, child.artifact_id, 1).status is ArtifactState.APPROVED


def test_requirement_reference_is_not_accepted_as_invalidation_root(tmp_path: Path):
    projects, handle, _versions, source, _child, _unrelated = _chain(tmp_path)
    req = ArtifactInputVersion(
        entity_id=CanonicalId.from_sequence(IdKind.REQUIREMENT, 1),
        version=1,
    )
    plan = _plan(handle.project_id, (req,), "continuity")
    with pytest.raises(SimulationLineageInvalidationError, match="artifact source"):
        invalidate_simulation_lineage(projects, plan)


def test_result_preserves_feedback_lineage_and_fingerprint(tmp_path: Path):
    projects, handle, _versions, source, _child, _unrelated = _chain(tmp_path)
    plan = _plan(handle.project_id, (_ref(source),), "continuity", "terminal_home")
    result = invalidate_simulation_lineage(projects, plan)

    assert result.project_id == plan.project_id
    assert result.requirement_version == plan.requirement_version
    assert result.source_artifacts == plan.source_artifacts
    assert result.runtime_run_id == plan.runtime_run_id
    assert result.verification_sha256 == plan.verification_sha256
    assert result.issue_fingerprints == tuple(route.issue.fingerprint for route in plan.routes)


def test_serialized_result_has_no_issue_id_task_retry_or_human_transition(tmp_path: Path):
    projects, handle, _versions, source, _child, _unrelated = _chain(tmp_path)
    raw = invalidate_simulation_lineage(
        projects,
        _plan(handle.project_id, (_ref(source),), "continuity"),
    ).to_dict()
    encoded = repr(raw)
    assert "issue_id" not in encoded
    assert "task_id" not in encoded
    assert "retry_attempt" not in encoded
    assert "needs_human" not in encoded


def test_wrong_inputs_fail_closed(tmp_path: Path):
    projects, handle, _versions, source, _child, _unrelated = _chain(tmp_path)
    plan = _plan(handle.project_id, (_ref(source),), "continuity")
    with pytest.raises(TypeError, match="ProjectStore"):
        invalidate_simulation_lineage(object(), plan)  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="SimulationIssueRolePlan"):
        invalidate_simulation_lineage(projects, object())  # type: ignore[arg-type]
