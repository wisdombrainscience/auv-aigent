from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

import pytest

import ai.simulation_feedback.human_gate as gate_module
import ai.simulation_feedback.retry as retry_module
from ai.artifacts import ArtifactInputVersion, ArtifactLifecycleStore, ArtifactVersionInput, ArtifactVersionStore
from ai.contracts.artifact_states import ArtifactState
from ai.contracts.provenance import ProvenanceClass
from ai.contracts.task_states import TaskState
from ai.contracts.validation_result import ValidationCheck, ValidationResult, ValidationStatus
from ai.project import Fact, FactSet, FactStore, ProjectStore, RequirementSetStatus, RequirementVersionStore
from ai.simulation_feedback import (
    MISSION_VERIFIER_CHECK_IDS,
    SIMULATION_NEEDS_HUMAN_CONTRACT_VERSION,
    SIMULATION_RETRY_REGISTRY_DB_FILENAME,
    SimulationHumanGateReason,
    SimulationReworkTaskGenerator,
    classify_simulation_issues,
    evaluate_simulation_needs_human,
    invalidate_simulation_lineage,
    route_simulation_issues,
)
from ai.simulation_feedback.parser import ParsedVerifierResult
from ai.tasks.lifecycle import TaskLifecycleStore
from ai.tasks.store import TaskStore
from ai.uav_bridge import PROJECT_MISSION_REQUEST_FACT_KEY


def _validation() -> ValidationResult:
    return ValidationResult.from_checks((ValidationCheck(check_id="deterministic", status=ValidationStatus.PASS, field=None, reason=None, details={}),))


def _approve(lifecycle, project_id, record) -> None:
    lifecycle.transition(project_id, record.artifact_id, record.version, ArtifactState.REVIEWED)
    lifecycle.transition(project_id, record.artifact_id, record.version, ArtifactState.VALIDATED, validation_result=_validation())
    lifecycle.transition(project_id, record.artifact_id, record.version, ArtifactState.APPROVED)


def _mission_request() -> dict:
    return {
        "scenario_id": "F12-7-GATE",
        "home_position_m": [0.0, 0.0],
        "waypoints": [{"north": 5.0, "east": 0.0, "altitude": 3.0}],
        "takeoff_altitude_m": 3.0,
        "return_altitude_m": 3.0,
        "position_tolerance_m": 0.5,
        "altitude_tolerance_m": 0.5,
        "horizontal_tolerance_m": 1.0,
        "ground_altitude_tolerance_m": 0.5,
        "continuity_tolerance_m": 1.0,
        "terminal_home_tolerance_m": 1.0,
        "timeout_s": 30.0,
    }


def _fixture(tmp_path: Path):
    projects = ProjectStore(tmp_path / "projects")
    handle = projects.create_project()
    RequirementVersionStore(projects).create_initial(
        handle.project_id,
        ["Mission shall complete and return home."],
        status=RequirementSetStatus.APPROVED,
    )
    FactStore(projects).save(FactSet(project_id=handle.project_id, facts=(Fact(key=PROJECT_MISSION_REQUEST_FACT_KEY, value=_mission_request(), provenance=ProvenanceClass.USER_FACT),)))
    versions = ArtifactVersionStore(projects)
    lifecycle = ArtifactLifecycleStore(projects)
    source = versions.create_artifact(
        handle.project_id,
        ArtifactVersionInput(type="mission_design", producer_task="TASK-000001", input_dependency_versions=(), content=b"source-v1", created_timestamp="2026-10-08T12:00:00Z"),
    )
    _approve(lifecycle, handle.project_id, source)
    return projects, handle, versions, lifecycle, source


def _parsed(project_id, source_ref, *, overall: str, run_id: str = "MISSION001-F12-7-ROOT"):
    failed = () if overall == "PASS" else ("continuity", "terminal_home")
    checks = {check_id: ("FAIL" if check_id in failed else "PASS") for check_id in MISSION_VERIFIER_CHECK_IDS}
    return ParsedVerifierResult(
        project_id=project_id,
        requirement_version=1,
        source_artifacts=(source_ref,),
        runtime_run_id=run_id,
        mission_id="MISSION-001",
        scenario_id="F12-7-GATE",
        overall=overall,
        checks=checks,
        failed_checks=failed,
        failed_step_ids=() if overall == "PASS" else ("waypoint_01",),
        max_transition_distance_m=0.0 if overall == "PASS" else 2.0,
        terminal_home_distance_m=0.0 if overall == "PASS" else 3.0,
        reason=None if overall == "PASS" else "verification failed",
        verification_sha256=("a" if overall == "PASS" else "b") * 64,
    )


def _generation(projects, handle, source, *, overall="FAIL", run_id="MISSION001-F12-7-ROOT"):
    ref = ArtifactInputVersion(entity_id=source.artifact_id, version=source.version)
    parsed = _parsed(handle.project_id, ref, overall=overall, run_id=run_id)
    plan = route_simulation_issues(classify_simulation_issues(parsed))
    invalidation = invalidate_simulation_lineage(projects, plan)
    return SimulationReworkTaskGenerator(projects).generate(plan, invalidation)


def _finish_tasks(projects, generation):
    lifecycle = TaskLifecycleStore(projects)
    for binding in generation.bindings:
        pid, tid = generation.project_id, binding.task.task_id
        lifecycle.transition_task(pid, tid, TaskState.READY)
        lifecycle.transition_task(pid, tid, TaskState.QUEUED)
        lifecycle.transition_task(pid, tid, TaskState.ASSIGNED)
        lifecycle.transition_task(pid, tid, TaskState.RUNNING, now_epoch_s=1.0)
        lifecycle.transition_task(pid, tid, TaskState.RESULT_PROPOSED)
        lifecycle.transition_task(pid, tid, TaskState.VALIDATING)
        lifecycle.transition_task(pid, tid, TaskState.DONE)


def _replacement(versions, lifecycle, project_id, previous, producer):
    record = versions.append_version(
        project_id,
        previous.artifact_id,
        ArtifactVersionInput(type=previous.type, producer_task=producer, input_dependency_versions=(), content=b"source-v2", created_timestamp="2026-10-08T12:01:00Z"),
    )
    _approve(lifecycle, project_id, record)
    return record


def _patch_parent_link_ok(monkeypatch):
    monkeypatch.setattr(retry_module, "_validate_parent_link", lambda links, generation: object())


def _registry(handle) -> Path:
    path = handle.layout.data_directories["simulation"] / SIMULATION_RETRY_REGISTRY_DB_FILENAME
    retry_module.initialize_simulation_retry_registry(path)
    return path


def _insert_attempt(path: Path, *, attempt: int, parent: str, root: str, state: str, runtime: str | None = None):
    con = sqlite3.connect(path)
    try:
        con.execute(
            """INSERT INTO simulation_retry_attempts(root_runtime_run_id,retry_attempt,parent_runtime_run_id,request_sha256,proposal_sha256,state,runtime_run_id,verification_sha256) VALUES(?,?,?,?,?,?,?,?)""",
            (root, attempt, parent, hashlib.sha256(f"r{attempt}".encode()).hexdigest(), hashlib.sha256(f"p{attempt}".encode()).hexdigest(), state, runtime, hashlib.sha256(f"v{attempt}".encode()).hexdigest() if state == "LINKED" else None),
        )
        con.commit()
    finally:
        con.close()


def test_contract_is_explicit():
    assert SIMULATION_NEEDS_HUMAN_CONTRACT_VERSION == "1.0"
    assert retry_module.MAX_SIMULATION_RETRY_ATTEMPTS == 2


def test_verifier_pass_requires_no_human_gate(tmp_path, monkeypatch):
    projects, handle, versions, lifecycle, source = _fixture(tmp_path)
    generation = _generation(projects, handle, source, overall="PASS")
    _patch_parent_link_ok(monkeypatch)
    decision = evaluate_simulation_needs_human(projects, tmp_path / "deployment", generation)
    assert decision.reason is SimulationHumanGateReason.VERIFIER_PASS
    assert decision.target_status is None
    assert decision.rework_task_ids == ()


def test_rework_in_progress_does_not_gate(tmp_path, monkeypatch):
    projects, handle, versions, lifecycle, source = _fixture(tmp_path)
    generation = _generation(projects, handle, source)
    _patch_parent_link_ok(monkeypatch)
    decision = evaluate_simulation_needs_human(projects, tmp_path / "deployment", generation)
    assert decision.reason is SimulationHumanGateReason.REWORK_IN_PROGRESS
    assert decision.needs_human is False


def test_existing_task_human_gate_is_preserved(tmp_path, monkeypatch):
    projects, handle, versions, lifecycle, source = _fixture(tmp_path)
    generation = _generation(projects, handle, source)
    _patch_parent_link_ok(monkeypatch)
    task = generation.bindings[0].task
    tl = TaskLifecycleStore(projects)
    tl.transition_task(handle.project_id, task.task_id, TaskState.BLOCKED)
    tl.transition_task(handle.project_id, task.task_id, TaskState.NEEDS_HUMAN)
    decision = evaluate_simulation_needs_human(projects, tmp_path / "deployment", generation)
    assert decision.reason is SimulationHumanGateReason.REWORK_TASK_NEEDS_HUMAN
    assert decision.target_status is TaskState.NEEDS_HUMAN


def test_terminal_rework_failure_requires_human(tmp_path, monkeypatch):
    projects, handle, versions, lifecycle, source = _fixture(tmp_path)
    generation = _generation(projects, handle, source)
    _patch_parent_link_ok(monkeypatch)
    task = generation.bindings[0].task
    tl = TaskLifecycleStore(projects)
    tl.transition_task(handle.project_id, task.task_id, TaskState.BLOCKED)
    tl.transition_task(handle.project_id, task.task_id, TaskState.FAILED)
    decision = evaluate_simulation_needs_human(projects, tmp_path / "deployment", generation)
    assert decision.reason is SimulationHumanGateReason.REWORK_TASK_TERMINAL_FAILURE
    assert decision.needs_human


def test_done_rework_with_valid_replacement_keeps_auto_retry_available(tmp_path, monkeypatch):
    projects, handle, versions, lifecycle, source = _fixture(tmp_path)
    generation = _generation(projects, handle, source)
    _patch_parent_link_ok(monkeypatch)
    _finish_tasks(projects, generation)
    _replacement(versions, lifecycle, handle.project_id, source, generation.bindings[0].task.task_id)
    path = handle.layout.data_directories["simulation"] / SIMULATION_RETRY_REGISTRY_DB_FILENAME
    assert not path.exists()
    decision = evaluate_simulation_needs_human(projects, tmp_path / "deployment", generation)
    assert decision.reason is SimulationHumanGateReason.AUTO_RETRY_AVAILABLE
    assert decision.retry_attempts_consumed == 0
    assert not path.exists(), "F12.7 evaluation must not create retry persistence"


def test_done_rework_without_replacement_requires_human(tmp_path, monkeypatch):
    projects, handle, versions, lifecycle, source = _fixture(tmp_path)
    generation = _generation(projects, handle, source)
    _patch_parent_link_ok(monkeypatch)
    _finish_tasks(projects, generation)
    decision = evaluate_simulation_needs_human(projects, tmp_path / "deployment", generation)
    assert decision.reason is SimulationHumanGateReason.RETRY_PRECONDITION_BLOCKED
    assert decision.needs_human


def test_retry_budget_exhaustion_routes_to_needs_human(tmp_path, monkeypatch):
    projects, handle, versions, lifecycle, source = _fixture(tmp_path)
    root = "MISSION001-F12-7-ROOT"
    generation = _generation(projects, handle, source, run_id=root)
    _patch_parent_link_ok(monkeypatch)
    _finish_tasks(projects, generation)
    _replacement(versions, lifecycle, handle.project_id, source, generation.bindings[0].task.task_id)
    path = _registry(handle)
    _insert_attempt(path, attempt=1, parent=root, root=root, state="LINKED", runtime="MISSION001-F12-7-R1")
    _insert_attempt(path, attempt=2, parent="MISSION001-F12-7-R1", root=root, state="LINKED", runtime="MISSION001-F12-7-R2")
    before = path.read_bytes()
    decision = evaluate_simulation_needs_human(projects, tmp_path / "deployment", generation)
    assert decision.reason is SimulationHumanGateReason.RETRY_BUDGET_EXHAUSTED
    assert decision.retry_attempts_consumed == 2
    assert decision.target_status is TaskState.NEEDS_HUMAN
    assert path.read_bytes() == before


def test_crash_reservation_requires_human_without_retry(tmp_path, monkeypatch):
    projects, handle, versions, lifecycle, source = _fixture(tmp_path)
    root = "MISSION001-F12-7-ROOT"
    generation = _generation(projects, handle, source, run_id=root)
    _patch_parent_link_ok(monkeypatch)
    _finish_tasks(projects, generation)
    _replacement(versions, lifecycle, handle.project_id, source, generation.bindings[0].task.task_id)
    path = _registry(handle)
    _insert_attempt(path, attempt=1, parent=root, root=root, state="RESERVED")
    decision = evaluate_simulation_needs_human(projects, tmp_path / "deployment", generation)
    assert decision.reason is SimulationHumanGateReason.RETRY_CRASH_RESERVATION
    assert decision.needs_human


def test_failed_retry_attempt_with_budget_remaining_allows_next_retry(tmp_path, monkeypatch):
    projects, handle, versions, lifecycle, source = _fixture(tmp_path)
    root = "MISSION001-F12-7-ROOT"
    generation = _generation(projects, handle, source, run_id=root)
    _patch_parent_link_ok(monkeypatch)
    _finish_tasks(projects, generation)
    _replacement(versions, lifecycle, handle.project_id, source, generation.bindings[0].task.task_id)
    path = _registry(handle)
    _insert_attempt(path, attempt=1, parent=root, root=root, state="FAILED")
    decision = evaluate_simulation_needs_human(projects, tmp_path / "deployment", generation)
    assert decision.reason is SimulationHumanGateReason.AUTO_RETRY_AVAILABLE
    assert decision.retry_attempts_consumed == 1
    assert decision.needs_human is False


def test_invalid_retry_registry_fails_closed_to_human(tmp_path, monkeypatch):
    projects, handle, versions, lifecycle, source = _fixture(tmp_path)
    generation = _generation(projects, handle, source)
    _patch_parent_link_ok(monkeypatch)
    _finish_tasks(projects, generation)
    path = handle.layout.data_directories["simulation"] / SIMULATION_RETRY_REGISTRY_DB_FILENAME
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE wrong_table(x INTEGER)")
    con.commit(); con.close()
    decision = evaluate_simulation_needs_human(projects, tmp_path / "deployment", generation)
    assert decision.reason is SimulationHumanGateReason.RETRY_REGISTRY_INVALID
    assert decision.needs_human


def test_invalid_parent_link_fails_closed_to_human(tmp_path, monkeypatch):
    projects, handle, versions, lifecycle, source = _fixture(tmp_path)
    generation = _generation(projects, handle, source)
    def bad_link(*args, **kwargs):
        raise retry_module.SimulationRetryConflictError("bad link")
    monkeypatch.setattr(retry_module, "_validate_parent_link", bad_link)
    decision = evaluate_simulation_needs_human(projects, tmp_path / "deployment", generation)
    assert decision.reason is SimulationHumanGateReason.PARENT_LINK_INVALID
    assert decision.needs_human


def test_decision_serialization_preserves_verifier_provenance(tmp_path, monkeypatch):
    projects, handle, versions, lifecycle, source = _fixture(tmp_path)
    generation = _generation(projects, handle, source, overall="PASS")
    _patch_parent_link_ok(monkeypatch)
    decision = evaluate_simulation_needs_human(projects, tmp_path / "deployment", generation)
    payload = decision.to_dict()
    assert payload["contract_version"] == "1.0"
    assert payload["provenance"] == "VERIFIER_RESULT"
    assert payload["target_status"] is None
