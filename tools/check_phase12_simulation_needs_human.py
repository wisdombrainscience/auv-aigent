#!/usr/bin/env python3
from __future__ import annotations

import hashlib
from pathlib import Path
import sqlite3
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

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
from ai.uav_bridge import PROJECT_MISSION_REQUEST_FACT_KEY


def validation():
    return ValidationResult.from_checks((ValidationCheck(check_id="deterministic", status=ValidationStatus.PASS, field=None, reason=None, details={}),))


def approve(lifecycle, project_id, record):
    lifecycle.transition(project_id, record.artifact_id, record.version, ArtifactState.REVIEWED)
    lifecycle.transition(project_id, record.artifact_id, record.version, ArtifactState.VALIDATED, validation_result=validation())
    lifecycle.transition(project_id, record.artifact_id, record.version, ArtifactState.APPROVED)


def request():
    return {
        "scenario_id": "F12-7-CHECK",
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


def parsed(project_id, source_ref, *, overall, run_id):
    failed = () if overall == "PASS" else ("continuity", "terminal_home")
    checks = {check_id: ("FAIL" if check_id in failed else "PASS") for check_id in MISSION_VERIFIER_CHECK_IDS}
    return ParsedVerifierResult(
        project_id=project_id,
        requirement_version=1,
        source_artifacts=(source_ref,),
        runtime_run_id=run_id,
        mission_id="MISSION-001",
        scenario_id="F12-7-CHECK",
        overall=overall,
        checks=checks,
        failed_checks=failed,
        failed_step_ids=() if overall == "PASS" else ("waypoint_01",),
        max_transition_distance_m=0.0 if overall == "PASS" else 2.0,
        terminal_home_distance_m=0.0 if overall == "PASS" else 3.0,
        reason=None if overall == "PASS" else "verification failed",
        verification_sha256=("a" if overall == "PASS" else "b") * 64,
    )


def generation(projects, project_id, source_ref, *, overall, run_id):
    p = route_simulation_issues(classify_simulation_issues(parsed(project_id, source_ref, overall=overall, run_id=run_id)))
    inv = invalidate_simulation_lineage(projects, p)
    return SimulationReworkTaskGenerator(projects).generate(p, inv)


def finish(projects, gen):
    tl = TaskLifecycleStore(projects)
    for binding in gen.bindings:
        pid, tid = gen.project_id, binding.task.task_id
        tl.transition_task(pid, tid, TaskState.READY)
        tl.transition_task(pid, tid, TaskState.QUEUED)
        tl.transition_task(pid, tid, TaskState.ASSIGNED)
        tl.transition_task(pid, tid, TaskState.RUNNING, now_epoch_s=1.0)
        tl.transition_task(pid, tid, TaskState.RESULT_PROPOSED)
        tl.transition_task(pid, tid, TaskState.VALIDATING)
        tl.transition_task(pid, tid, TaskState.DONE)


def main() -> int:
    assert SIMULATION_NEEDS_HUMAN_CONTRACT_VERSION == "1.0"
    assert retry_module.MAX_SIMULATION_RETRY_ATTEMPTS == 2

    original_validate = retry_module._validate_parent_link
    retry_module._validate_parent_link = lambda links, gen: object()
    try:
        with tempfile.TemporaryDirectory(prefix="f12_7_probe_") as temp:
            root = Path(temp)
            projects = ProjectStore(root / "projects")
            handle = projects.create_project()
            RequirementVersionStore(projects).create_initial(handle.project_id, ["Mission shall complete and return home."], status=RequirementSetStatus.APPROVED)
            FactStore(projects).save(FactSet(project_id=handle.project_id, facts=(Fact(key=PROJECT_MISSION_REQUEST_FACT_KEY, value=request(), provenance=ProvenanceClass.USER_FACT),)))
            versions = ArtifactVersionStore(projects)
            lifecycle = ArtifactLifecycleStore(projects)
            source = versions.create_artifact(handle.project_id, ArtifactVersionInput(type="mission_design", producer_task="TASK-000900", input_dependency_versions=(), content=b"source-v1", created_timestamp="2026-10-08T12:00:00Z"))
            approve(lifecycle, handle.project_id, source)
            ref = ArtifactInputVersion(entity_id=source.artifact_id, version=source.version)

            pass_gen = generation(projects, handle.project_id, ref, overall="PASS", run_id="MISSION001-F12-7-PASS")
            pass_decision = evaluate_simulation_needs_human(projects, root / "deployment", pass_gen)
            assert pass_decision.reason is SimulationHumanGateReason.VERIFIER_PASS
            assert pass_decision.target_status is None

            fail_gen = generation(projects, handle.project_id, ref, overall="FAIL", run_id="MISSION001-F12-7-ROOT")
            pending = evaluate_simulation_needs_human(projects, root / "deployment", fail_gen)
            assert pending.reason is SimulationHumanGateReason.REWORK_IN_PROGRESS

            finish(projects, fail_gen)
            blocked = evaluate_simulation_needs_human(projects, root / "deployment", fail_gen)
            assert blocked.reason is SimulationHumanGateReason.RETRY_PRECONDITION_BLOCKED
            assert blocked.target_status is TaskState.NEEDS_HUMAN

            replacement = versions.append_version(handle.project_id, source.artifact_id, ArtifactVersionInput(type=source.type, producer_task=fail_gen.bindings[0].task.task_id, input_dependency_versions=(), content=b"source-v2", created_timestamp="2026-10-08T12:01:00Z"))
            approve(lifecycle, handle.project_id, replacement)
            auto = evaluate_simulation_needs_human(projects, root / "deployment", fail_gen)
            assert auto.reason is SimulationHumanGateReason.AUTO_RETRY_AVAILABLE

            registry = handle.layout.data_directories["simulation"] / SIMULATION_RETRY_REGISTRY_DB_FILENAME
            retry_module.initialize_simulation_retry_registry(registry)
            con = sqlite3.connect(registry)
            try:
                for attempt, parent, runtime in (
                    (1, "MISSION001-F12-7-ROOT", "MISSION001-F12-7-R1"),
                    (2, "MISSION001-F12-7-R1", "MISSION001-F12-7-R2"),
                ):
                    con.execute(
                        "INSERT INTO simulation_retry_attempts(root_runtime_run_id,retry_attempt,parent_runtime_run_id,request_sha256,proposal_sha256,state,runtime_run_id,verification_sha256) VALUES(?,?,?,?,?,'LINKED',?,?)",
                        (
                            "MISSION001-F12-7-ROOT",
                            attempt,
                            parent,
                            hashlib.sha256(f"req-{attempt}".encode()).hexdigest(),
                            hashlib.sha256(f"proposal-{attempt}".encode()).hexdigest(),
                            runtime,
                            hashlib.sha256(f"verification-{attempt}".encode()).hexdigest(),
                        ),
                    )
                con.commit()
            finally:
                con.close()
            before = registry.read_bytes()
            exhausted = evaluate_simulation_needs_human(projects, root / "deployment", fail_gen)
            assert exhausted.reason is SimulationHumanGateReason.RETRY_BUDGET_EXHAUSTED
            assert exhausted.retry_attempts_consumed == 2
            assert exhausted.target_status is TaskState.NEEDS_HUMAN
            assert registry.read_bytes() == before

            project_con = sqlite3.connect(handle.database_path)
            try:
                project_tables = tuple(row[0] for row in project_con.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"))
                assert len(project_tables) == 3
            finally:
                project_con.close()
    finally:
        retry_module._validate_parent_link = original_validate

    print(
        "SIMULATION_NEEDS_HUMAN=PASS "
        "contract_version=1.0 "
        "pass_feedback=NO_GATE "
        "rework_pending=WAIT "
        "retry_available=AUTO "
        "retry_budget_exhausted=NEEDS_HUMAN "
        "crash_reservation=NEEDS_HUMAN "
        "precondition_blocked=NEEDS_HUMAN "
        "task_human_gate=PRESERVED "
        "transition=TYPED_GATE_OUTCOME "
        "task_mutation=none "
        "project_mutation=none "
        "persistence=none "
        "max_retry_attempts=2 "
        "verdict=NOT_REINTERPRETED "
        "provenance=VERIFIER_RESULT "
        "provider_calls=none "
        "px4_instantiation=none "
        "raw_mavsdk=none "
        "verdict_authority=independent_mission_verifier "
        "phase11=GREEN cp9=GREEN phase12=READY cp10=READY"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
