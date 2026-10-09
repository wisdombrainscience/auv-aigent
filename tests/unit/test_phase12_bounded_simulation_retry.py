from __future__ import annotations

import asyncio
import hashlib
import sqlite3
from pathlib import Path

import pytest

import ai.simulation_feedback.retry as retry_module
from ai.artifacts import ArtifactInputVersion, ArtifactLifecycleStore, ArtifactVersionInput, ArtifactVersionStore
from ai.contracts.artifact_states import ArtifactState
from ai.contracts.provenance import ProvenanceClass
from ai.contracts.task_states import TaskState
from ai.contracts.validation_result import ValidationCheck, ValidationResult, ValidationStatus
from ai.project import Fact, FactSet, FactStore, ProjectStore, RequirementSetStatus, RequirementVersionStore
from ai.simulation_feedback import (
    BoundedSimulationRetryController,
    MAX_SIMULATION_RETRY_ATTEMPTS,
    MISSION_VERIFIER_CHECK_IDS,
    SIMULATION_RETRY_CONTRACT_VERSION,
    SIMULATION_RETRY_REGISTRY_DB_FILENAME,
    SIMULATION_RETRY_REGISTRY_SCHEMA_VERSION,
    SimulationRetryBudgetExhaustedError,
    SimulationRetryConflictError,
    SimulationReworkTaskGenerator,
    classify_simulation_issues,
    invalidate_simulation_lineage,
    route_simulation_issues,
)
from ai.simulation_feedback.parser import ParsedVerifierResult
from ai.tasks.lifecycle import TaskLifecycleStore
from ai.uav_bridge import PROJECT_MISSION_REQUEST_FACT_KEY, UAVRunLinkRecord


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


def _approve(lifecycle: ArtifactLifecycleStore, project_id, record) -> None:
    lifecycle.transition(project_id, record.artifact_id, record.version, ArtifactState.REVIEWED)
    lifecycle.transition(
        project_id,
        record.artifact_id,
        record.version,
        ArtifactState.VALIDATED,
        validation_result=_validation(),
    )
    lifecycle.transition(project_id, record.artifact_id, record.version, ArtifactState.APPROVED)


def _mission_request(scenario_id: str = "F12-6-RETRY") -> dict:
    return {
        "scenario_id": scenario_id,
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
    FactStore(projects).save(
        FactSet(
            project_id=handle.project_id,
            facts=(
                Fact(
                    key=PROJECT_MISSION_REQUEST_FACT_KEY,
                    value=_mission_request(),
                    provenance=ProvenanceClass.USER_FACT,
                ),
            ),
        )
    )
    versions = ArtifactVersionStore(projects)
    lifecycle = ArtifactLifecycleStore(projects)
    source = versions.create_artifact(
        handle.project_id,
        ArtifactVersionInput(
            type="mission_design",
            producer_task="TASK-000001",
            input_dependency_versions=(),
            content=b"source-v1",
            created_timestamp="2026-10-08T12:00:00Z",
        ),
    )
    _approve(lifecycle, handle.project_id, source)
    return projects, handle, versions, lifecycle, source


def _plan(project_id, source_ref: ArtifactInputVersion, *, run_id: str, verification: str):
    failed = ("continuity", "terminal_home")
    checks = {
        check_id: ("FAIL" if check_id in failed else "PASS")
        for check_id in MISSION_VERIFIER_CHECK_IDS
    }
    parsed = ParsedVerifierResult(
        project_id=project_id,
        requirement_version=1,
        source_artifacts=(source_ref,),
        runtime_run_id=run_id,
        mission_id="MISSION-001",
        scenario_id="F12-6-RETRY",
        overall="FAIL",
        checks=checks,
        failed_checks=failed,
        failed_step_ids=("waypoint_01",),
        max_transition_distance_m=2.0,
        terminal_home_distance_m=3.0,
        reason="verification failed",
        verification_sha256=verification,
    )
    return route_simulation_issues(classify_simulation_issues(parsed))


def _generation(projects, plan):
    invalidation = invalidate_simulation_lineage(projects, plan)
    return SimulationReworkTaskGenerator(projects).generate(plan, invalidation)


def _finish_task(projects: ProjectStore, generation) -> None:
    lifecycle = TaskLifecycleStore(projects)
    for binding in generation.bindings:
        project_id = generation.project_id
        task_id = binding.task.task_id
        lifecycle.transition_task(project_id, task_id, TaskState.READY)
        lifecycle.transition_task(project_id, task_id, TaskState.QUEUED)
        lifecycle.transition_task(project_id, task_id, TaskState.ASSIGNED)
        lifecycle.transition_task(project_id, task_id, TaskState.RUNNING, now_epoch_s=1.0)
        lifecycle.transition_task(project_id, task_id, TaskState.RESULT_PROPOSED)
        lifecycle.transition_task(project_id, task_id, TaskState.VALIDATING)
        lifecycle.transition_task(project_id, task_id, TaskState.DONE)


def _replacement(versions, lifecycle, project_id, previous, producer_task, version_label: str):
    record = versions.append_version(
        project_id,
        previous.artifact_id,
        ArtifactVersionInput(
            type=previous.type,
            producer_task=producer_task,
            input_dependency_versions=(),
            content=version_label.encode("utf-8"),
            created_timestamp="2026-10-08T12:01:00Z",
        ),
    )
    _approve(lifecycle, project_id, record)
    return record


def _link(project_id, source_ref, *, run_id: str, verification: str) -> UAVRunLinkRecord:
    h = hashlib.sha256(run_id.encode("utf-8")).hexdigest()
    return UAVRunLinkRecord(
        project_id=project_id,
        requirement_version=1,
        source_artifacts=(source_ref,),
        proposal_sha256=h,
        scenario_id="F12-6-RETRY",
        mission_id="MISSION-001",
        runtime_run_id=run_id,
        runtime_result_sha256=h,
        attempt_relpath=f"MISSION-001/F12-6-RETRY/{run_id}",
        finalized_sha256=h,
        mission_input_sha256=h,
        mission_steps_sha256=h,
        verification_sha256=verification,
    )


class _RuntimeHarness:
    def __init__(self, monkeypatch, project_id, links: dict[str, UAVRunLinkRecord]):
        self.links = links
        self.calls = []
        self.counter = 0

        def open_link(_store, pid, run_id):
            assert pid == project_id
            try:
                return self.links[run_id]
            except KeyError as exc:
                raise retry_module.UAVRunLinkNotFoundError(run_id) from exc

        async def invoke(_deployment, proposal, *, adapter, step_hook=None, step_event_hook=None):
            self.counter += 1
            self.calls.append((proposal, adapter, step_hook, step_event_hook))
            return {"run_id": f"MISSION001-F12-6-RETRY-{self.counter}"}

        def link(_projects, _deployment, proposal, runtime_result):
            run_id = runtime_result["run_id"]
            verification = hashlib.sha256((run_id + "-verification").encode()).hexdigest()
            record = _link(
                project_id,
                proposal.source_artifacts[0],
                run_id=run_id,
                verification=verification,
            )
            self.links[run_id] = record
            return record

        monkeypatch.setattr(retry_module.UAVRunLinkStore, "open", open_link)
        monkeypatch.setattr(retry_module, "invoke_existing_runtime", invoke)
        monkeypatch.setattr(retry_module, "link_uav_run_to_project", link)


def _ready_generation(tmp_path: Path, monkeypatch, *, parent_run="MISSION001-F12-6-ROOT", verification="a" * 64):
    projects, handle, versions, lifecycle, source = _fixture(tmp_path)
    source_ref = ArtifactInputVersion(entity_id=source.artifact_id, version=source.version)
    plan = _plan(handle.project_id, source_ref, run_id=parent_run, verification=verification)
    generation = _generation(projects, plan)
    links = {parent_run: _link(handle.project_id, source_ref, run_id=parent_run, verification=verification)}
    harness = _RuntimeHarness(monkeypatch, handle.project_id, links)
    _finish_task(projects, generation)
    replacement = _replacement(
        versions,
        lifecycle,
        handle.project_id,
        source,
        generation.bindings[0].task.task_id,
        "source-v2",
    )
    return projects, handle, versions, lifecycle, source, replacement, generation, harness


def test_contract_and_frozen_retry_bound_are_explicit():
    assert SIMULATION_RETRY_CONTRACT_VERSION == "1.0"
    assert SIMULATION_RETRY_REGISTRY_SCHEMA_VERSION == 1
    assert SIMULATION_RETRY_REGISTRY_DB_FILENAME == "simulation_retries.sqlite3"
    assert MAX_SIMULATION_RETRY_ATTEMPTS == 2


def test_retry_requires_done_rework_tasks_before_runtime(tmp_path: Path, monkeypatch):
    projects, handle, versions, lifecycle, source = _fixture(tmp_path)
    source_ref = ArtifactInputVersion(entity_id=source.artifact_id, version=1)
    plan = _plan(handle.project_id, source_ref, run_id="MISSION001-NOT-DONE", verification="a" * 64)
    generation = _generation(projects, plan)
    links = {generation.runtime_run_id: _link(handle.project_id, source_ref, run_id=generation.runtime_run_id, verification=generation.verification_sha256)}
    harness = _RuntimeHarness(monkeypatch, handle.project_id, links)
    _replacement(versions, lifecycle, handle.project_id, source, generation.bindings[0].task.task_id, "source-v2")

    with pytest.raises(SimulationRetryConflictError, match="must be DONE"):
        asyncio.run(BoundedSimulationRetryController(projects, tmp_path / "deployment").retry(generation, adapter=object()))
    assert harness.calls == []


def test_retry_requires_new_current_approved_source_from_rework_task(tmp_path: Path, monkeypatch):
    projects, handle, versions, lifecycle, source = _fixture(tmp_path)
    source_ref = ArtifactInputVersion(entity_id=source.artifact_id, version=1)
    plan = _plan(handle.project_id, source_ref, run_id="MISSION001-NO-REPLACEMENT", verification="b" * 64)
    generation = _generation(projects, plan)
    links = {generation.runtime_run_id: _link(handle.project_id, source_ref, run_id=generation.runtime_run_id, verification=generation.verification_sha256)}
    harness = _RuntimeHarness(monkeypatch, handle.project_id, links)
    _finish_task(projects, generation)

    with pytest.raises(SimulationRetryConflictError, match="newer current APPROVED"):
        asyncio.run(BoundedSimulationRetryController(projects, tmp_path / "deployment").retry(generation, adapter=object()))
    assert harness.calls == []


def test_retry_rejects_replacement_from_unrelated_task(tmp_path: Path, monkeypatch):
    projects, handle, versions, lifecycle, source = _fixture(tmp_path)
    source_ref = ArtifactInputVersion(entity_id=source.artifact_id, version=1)
    plan = _plan(handle.project_id, source_ref, run_id="MISSION001-WRONG-PRODUCER", verification="c" * 64)
    generation = _generation(projects, plan)
    links = {generation.runtime_run_id: _link(handle.project_id, source_ref, run_id=generation.runtime_run_id, verification=generation.verification_sha256)}
    harness = _RuntimeHarness(monkeypatch, handle.project_id, links)
    _finish_task(projects, generation)
    _replacement(versions, lifecycle, handle.project_id, source, "TASK-000999", "source-v2")

    with pytest.raises(SimulationRetryConflictError, match="produced by an F12.5 rework task"):
        asyncio.run(BoundedSimulationRetryController(projects, tmp_path / "deployment").retry(generation, adapter=object()))
    assert harness.calls == []


def test_first_retry_uses_existing_bridge_and_links_without_interpreting_verdict(tmp_path: Path, monkeypatch):
    projects, handle, _versions, _lifecycle, _source, replacement, generation, harness = _ready_generation(tmp_path, monkeypatch)
    adapter = object()
    result = asyncio.run(
        BoundedSimulationRetryController(projects, tmp_path / "deployment").retry(
            generation,
            adapter=adapter,
        )
    )

    assert result.retry_attempt == 1
    assert result.root_runtime_run_id == generation.runtime_run_id
    assert result.parent_runtime_run_id == generation.runtime_run_id
    assert result.source_artifacts == (ArtifactInputVersion(entity_id=replacement.artifact_id, version=2),)
    assert result.runtime_run_id == "MISSION001-F12-6-RETRY-1"
    assert result.reused_existing is False
    assert result.simulation_provenance is ProvenanceClass.SIMULATION_RESULT
    assert result.verifier_provenance is ProvenanceClass.VERIFIER_RESULT
    assert len(harness.calls) == 1
    proposal, passed_adapter, _, _ = harness.calls[0]
    assert passed_adapter is adapter
    assert proposal.requirement_version == generation.requirement_version
    assert proposal.scenario_id == generation.scenario_id
    assert proposal.mission_id == generation.mission_id
    assert proposal.source_artifacts == result.source_artifacts
    raw = result.to_dict()
    assert "overall" not in raw
    assert "verdict" not in raw
    assert "needs_human" not in raw


def test_completed_retry_is_restart_idempotent_and_does_not_reinvoke_runtime(tmp_path: Path, monkeypatch):
    projects, _handle, _versions, _lifecycle, _source, _replacement, generation, harness = _ready_generation(tmp_path, monkeypatch)
    root = tmp_path / "deployment"
    first = asyncio.run(BoundedSimulationRetryController(projects, root).retry(generation, adapter=object()))
    second = asyncio.run(BoundedSimulationRetryController(projects, root).retry(generation, adapter=object()))

    assert first.runtime_run_id == second.runtime_run_id
    assert first.retry_attempt == second.retry_attempt == 1
    assert first.reused_existing is False
    assert second.reused_existing is True
    assert len(harness.calls) == 1


def test_retry_chain_allows_exactly_two_linked_retries_then_exhausts(tmp_path: Path, monkeypatch):
    projects, handle, versions, lifecycle, _source1, source2, generation1, harness = _ready_generation(tmp_path, monkeypatch)
    controller = BoundedSimulationRetryController(projects, tmp_path / "deployment")
    first = asyncio.run(controller.retry(generation1, adapter=object()))
    assert first.retry_attempt == 1

    source2_ref = ArtifactInputVersion(entity_id=source2.artifact_id, version=source2.version)
    plan2 = _plan(handle.project_id, source2_ref, run_id=first.runtime_run_id, verification=first.verification_sha256)
    generation2 = _generation(projects, plan2)
    _finish_task(projects, generation2)
    source3 = _replacement(versions, lifecycle, handle.project_id, source2, generation2.bindings[0].task.task_id, "source-v3")
    second = asyncio.run(controller.retry(generation2, adapter=object()))
    assert second.retry_attempt == 2
    assert second.root_runtime_run_id == generation1.runtime_run_id

    source3_ref = ArtifactInputVersion(entity_id=source3.artifact_id, version=source3.version)
    plan3 = _plan(handle.project_id, source3_ref, run_id=second.runtime_run_id, verification=second.verification_sha256)
    generation3 = _generation(projects, plan3)
    _finish_task(projects, generation3)
    _replacement(versions, lifecycle, handle.project_id, source3, generation3.bindings[0].task.task_id, "source-v4")

    before = len(harness.calls)
    with pytest.raises(SimulationRetryBudgetExhaustedError, match="budget exhausted"):
        asyncio.run(controller.retry(generation3, adapter=object()))
    assert len(harness.calls) == before == 2


def test_runtime_failure_consumes_one_bounded_attempt_and_can_use_second(tmp_path: Path, monkeypatch):
    projects, handle, _versions, _lifecycle, _source, _replacement, generation, harness = _ready_generation(tmp_path, monkeypatch)
    calls = {"count": 0}

    async def flaky(_deployment, proposal, *, adapter, step_hook=None, step_event_hook=None):
        calls["count"] += 1
        if calls["count"] == 1:
            raise RuntimeError("runtime failed")
        return {"run_id": "MISSION001-F12-6-AFTER-FAIL"}

    monkeypatch.setattr(retry_module, "invoke_existing_runtime", flaky)
    controller = BoundedSimulationRetryController(projects, tmp_path / "deployment")
    with pytest.raises(RuntimeError, match="runtime failed"):
        asyncio.run(controller.retry(generation, adapter=object()))

    result = asyncio.run(controller.retry(generation, adapter=object()))
    assert result.retry_attempt == 2
    assert calls["count"] == 2


def test_reserved_retry_fails_closed_instead_of_duplicate_runtime(tmp_path: Path, monkeypatch):
    projects, handle, _versions, _lifecycle, _source, _replacement, generation, harness = _ready_generation(tmp_path, monkeypatch)
    controller = BoundedSimulationRetryController(projects, tmp_path / "deployment")

    # Reserve exactly the request the controller will use, but simulate a crash
    # before runtime invocation or state completion.
    tasks = retry_module._validate_rework_tasks_done(projects, generation)
    replacements = retry_module._replacement_sources(projects, generation, tasks)
    proposal = retry_module._proposal_for_retry(projects, generation, replacements)
    registry = retry_module._registry_path(projects, handle.project_id)
    retry_module._reserve_retry(
        registry,
        parent_runtime_run_id=generation.runtime_run_id,
        request_sha256=retry_module._request_sha256(generation, tasks, proposal),
        proposal_sha256=retry_module._proposal_sha256(proposal),
    )

    with pytest.raises(SimulationRetryConflictError, match="in-progress reserved retry"):
        asyncio.run(controller.retry(generation, adapter=object()))
    assert harness.calls == []


def test_requirement_change_blocks_automatic_retry_before_runtime(tmp_path: Path, monkeypatch):
    projects, handle, _versions, _lifecycle, _source, _replacement, generation, harness = _ready_generation(tmp_path, monkeypatch)
    RequirementVersionStore(projects).append_version(
        handle.project_id,
        ["Changed requirement requires human review."],
        status=RequirementSetStatus.APPROVED,
    )

    with pytest.raises(SimulationRetryConflictError, match="requirement version changed"):
        asyncio.run(BoundedSimulationRetryController(projects, tmp_path / "deployment").retry(generation, adapter=object()))
    assert harness.calls == []


def test_scenario_change_is_new_scenario_not_retry(tmp_path: Path, monkeypatch):
    projects, handle, _versions, _lifecycle, _source, _replacement, generation, harness = _ready_generation(tmp_path, monkeypatch)
    FactStore(projects).save(
        FactSet(
            project_id=handle.project_id,
            facts=(
                Fact(
                    key=PROJECT_MISSION_REQUEST_FACT_KEY,
                    value=_mission_request("F12-6-NEW-SCENARIO"),
                    provenance=ProvenanceClass.USER_FACT,
                ),
            ),
        )
    )

    with pytest.raises(SimulationRetryConflictError, match="new scenario"):
        asyncio.run(BoundedSimulationRetryController(projects, tmp_path / "deployment").retry(generation, adapter=object()))
    assert harness.calls == []


def test_retry_registry_is_project_local_bounded_and_separate_from_project_schema(tmp_path: Path, monkeypatch):
    projects, handle, _versions, _lifecycle, _source, _replacement, generation, _harness = _ready_generation(tmp_path, monkeypatch)
    asyncio.run(BoundedSimulationRetryController(projects, tmp_path / "deployment").retry(generation, adapter=object()))
    path = handle.layout.data_directories["simulation"] / SIMULATION_RETRY_REGISTRY_DB_FILENAME
    assert path.is_file()

    con = sqlite3.connect(path)
    try:
        tables = tuple(row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"))
        assert tables == ("simulation_retry_attempts", "simulation_retry_registry_meta")
        rows = con.execute("SELECT retry_attempt,state FROM simulation_retry_attempts ORDER BY retry_attempt").fetchall()
        assert rows == [(1, "LINKED")]
    finally:
        con.close()

    project_con = sqlite3.connect(handle.database_path)
    try:
        project_tables = tuple(row[0] for row in project_con.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"))
        assert len(project_tables) == 3
    finally:
        project_con.close()


def test_wrong_inputs_and_none_adapter_fail_before_retry_state(tmp_path: Path):
    projects, _handle, _versions, _lifecycle, _source = _fixture(tmp_path)
    controller = BoundedSimulationRetryController(projects, tmp_path / "deployment")
    with pytest.raises(TypeError, match="SimulationReworkTaskGenerationResult"):
        asyncio.run(controller.retry(object(), adapter=object()))  # type: ignore[arg-type]
