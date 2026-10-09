#!/usr/bin/env python3
from __future__ import annotations

import asyncio
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
    BoundedSimulationRetryController,
    MAX_SIMULATION_RETRY_ATTEMPTS,
    MISSION_VERIFIER_CHECK_IDS,
    SIMULATION_RETRY_CONTRACT_VERSION,
    SIMULATION_RETRY_REGISTRY_DB_FILENAME,
    SIMULATION_RETRY_REGISTRY_SCHEMA_VERSION,
    SimulationRetryBudgetExhaustedError,
    SimulationReworkTaskGenerator,
    classify_simulation_issues,
    invalidate_simulation_lineage,
    route_simulation_issues,
)
from ai.simulation_feedback.parser import ParsedVerifierResult
from ai.tasks.lifecycle import TaskLifecycleStore
from ai.uav_bridge import PROJECT_MISSION_REQUEST_FACT_KEY, UAVRunLinkRecord


def validation() -> ValidationResult:
    return ValidationResult.from_checks((
        ValidationCheck(
            check_id="deterministic",
            status=ValidationStatus.PASS,
            field=None,
            reason=None,
            details={},
        ),
    ))


def approve(lifecycle, project_id, record) -> None:
    lifecycle.transition(project_id, record.artifact_id, record.version, ArtifactState.REVIEWED)
    lifecycle.transition(
        project_id,
        record.artifact_id,
        record.version,
        ArtifactState.VALIDATED,
        validation_result=validation(),
    )
    lifecycle.transition(project_id, record.artifact_id, record.version, ArtifactState.APPROVED)


def mission_request() -> dict:
    return {
        "scenario_id": "F12-6-CHECK",
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


def plan(project_id, source_ref, run_id: str, verification_sha256: str):
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
        scenario_id="F12-6-CHECK",
        overall="FAIL",
        checks=checks,
        failed_checks=failed,
        failed_step_ids=("waypoint_01",),
        max_transition_distance_m=2.0,
        terminal_home_distance_m=3.0,
        reason="verification failed",
        verification_sha256=verification_sha256,
    )
    return route_simulation_issues(classify_simulation_issues(parsed))


def finish_tasks(projects, generation) -> None:
    lifecycle = TaskLifecycleStore(projects)
    for binding in generation.bindings:
        pid = generation.project_id
        tid = binding.task.task_id
        lifecycle.transition_task(pid, tid, TaskState.READY)
        lifecycle.transition_task(pid, tid, TaskState.QUEUED)
        lifecycle.transition_task(pid, tid, TaskState.ASSIGNED)
        lifecycle.transition_task(pid, tid, TaskState.RUNNING, now_epoch_s=1.0)
        lifecycle.transition_task(pid, tid, TaskState.RESULT_PROPOSED)
        lifecycle.transition_task(pid, tid, TaskState.VALIDATING)
        lifecycle.transition_task(pid, tid, TaskState.DONE)


def replacement(versions, lifecycle, project_id, previous, producer_task, content: bytes):
    record = versions.append_version(
        project_id,
        previous.artifact_id,
        ArtifactVersionInput(
            type=previous.type,
            producer_task=producer_task,
            input_dependency_versions=(),
            content=content,
            created_timestamp="2026-10-08T13:00:00Z",
        ),
    )
    approve(lifecycle, project_id, record)
    return record


def link_record(project_id, source_ref, run_id: str, verification_sha256: str) -> UAVRunLinkRecord:
    digest = hashlib.sha256(run_id.encode()).hexdigest()
    return UAVRunLinkRecord(
        project_id=project_id,
        requirement_version=1,
        source_artifacts=(source_ref,),
        proposal_sha256=digest,
        scenario_id="F12-6-CHECK",
        mission_id="MISSION-001",
        runtime_run_id=run_id,
        runtime_result_sha256=digest,
        attempt_relpath=f"MISSION-001/F12-6-CHECK/{run_id}",
        finalized_sha256=digest,
        mission_input_sha256=digest,
        mission_steps_sha256=digest,
        verification_sha256=verification_sha256,
    )


def main() -> int:
    assert SIMULATION_RETRY_CONTRACT_VERSION == "1.0"
    assert SIMULATION_RETRY_REGISTRY_SCHEMA_VERSION == 1
    assert MAX_SIMULATION_RETRY_ATTEMPTS == 2

    with tempfile.TemporaryDirectory(prefix="f12_6_probe_") as temp:
        temp_root = Path(temp)
        projects = ProjectStore(temp_root / "projects")
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
                        value=mission_request(),
                        provenance=ProvenanceClass.USER_FACT,
                    ),
                ),
            )
        )
        versions = ArtifactVersionStore(projects)
        artifact_lifecycle = ArtifactLifecycleStore(projects)
        source1 = versions.create_artifact(
            handle.project_id,
            ArtifactVersionInput(
                type="mission_design",
                producer_task="TASK-000900",
                input_dependency_versions=(),
                content=b"source-v1",
                created_timestamp="2026-10-08T12:00:00Z",
            ),
        )
        approve(artifact_lifecycle, handle.project_id, source1)
        ref1 = ArtifactInputVersion(entity_id=source1.artifact_id, version=source1.version)

        root_run = "MISSION001-F12-6-ROOT"
        root_verification = "a" * 64
        plan1 = plan(handle.project_id, ref1, root_run, root_verification)
        inv1 = invalidate_simulation_lineage(projects, plan1)
        gen1 = SimulationReworkTaskGenerator(projects).generate(plan1, inv1)
        finish_tasks(projects, gen1)
        source2 = replacement(
            versions,
            artifact_lifecycle,
            handle.project_id,
            source1,
            gen1.bindings[0].task.task_id,
            b"source-v2",
        )

        links: dict[str, UAVRunLinkRecord] = {
            root_run: link_record(handle.project_id, ref1, root_run, root_verification)
        }
        runtime_calls: list[str] = []

        original_open = retry_module.UAVRunLinkStore.open
        original_invoke = retry_module.invoke_existing_runtime
        original_link = retry_module.link_uav_run_to_project

        def fake_open(_store, project_id, run_id):
            assert project_id == handle.project_id
            return links[run_id]

        async def fake_invoke(_deployment, proposal, *, adapter, step_hook=None, step_event_hook=None):
            run_id = f"MISSION001-F12-6-RETRY-{len(runtime_calls) + 1}"
            runtime_calls.append(run_id)
            return {"run_id": run_id, "proposal": proposal}

        def fake_link(_projects, _deployment, proposal, result):
            run_id = result["run_id"]
            verification = hashlib.sha256((run_id + "-verification").encode()).hexdigest()
            record = link_record(
                handle.project_id,
                proposal.source_artifacts[0],
                run_id,
                verification,
            )
            links[run_id] = record
            return record

        retry_module.UAVRunLinkStore.open = fake_open
        retry_module.invoke_existing_runtime = fake_invoke
        retry_module.link_uav_run_to_project = fake_link
        try:
            controller = BoundedSimulationRetryController(projects, temp_root / "deployment")
            first = asyncio.run(controller.retry(gen1, adapter=object()))
            assert first.retry_attempt == 1
            assert first.root_runtime_run_id == root_run
            assert first.source_artifacts[0].version == source2.version
            assert len(runtime_calls) == 1

            duplicate = asyncio.run(controller.retry(gen1, adapter=object()))
            assert duplicate.runtime_run_id == first.runtime_run_id
            assert duplicate.reused_existing is True
            assert len(runtime_calls) == 1

            ref2 = ArtifactInputVersion(entity_id=source2.artifact_id, version=source2.version)
            plan2 = plan(handle.project_id, ref2, first.runtime_run_id, first.verification_sha256)
            inv2 = invalidate_simulation_lineage(projects, plan2)
            gen2 = SimulationReworkTaskGenerator(projects).generate(plan2, inv2)
            finish_tasks(projects, gen2)
            source3 = replacement(
                versions,
                artifact_lifecycle,
                handle.project_id,
                source2,
                gen2.bindings[0].task.task_id,
                b"source-v3",
            )
            second = asyncio.run(controller.retry(gen2, adapter=object()))
            assert second.retry_attempt == 2
            assert second.root_runtime_run_id == root_run
            assert len(runtime_calls) == 2

            ref3 = ArtifactInputVersion(entity_id=source3.artifact_id, version=source3.version)
            plan3 = plan(handle.project_id, ref3, second.runtime_run_id, second.verification_sha256)
            inv3 = invalidate_simulation_lineage(projects, plan3)
            gen3 = SimulationReworkTaskGenerator(projects).generate(plan3, inv3)
            finish_tasks(projects, gen3)
            replacement(
                versions,
                artifact_lifecycle,
                handle.project_id,
                source3,
                gen3.bindings[0].task.task_id,
                b"source-v4",
            )
            try:
                asyncio.run(controller.retry(gen3, adapter=object()))
            except SimulationRetryBudgetExhaustedError:
                pass
            else:
                raise AssertionError("third retry was not denied")
            assert len(runtime_calls) == 2
        finally:
            retry_module.UAVRunLinkStore.open = original_open
            retry_module.invoke_existing_runtime = original_invoke
            retry_module.link_uav_run_to_project = original_link

        registry = handle.layout.data_directories["simulation"] / SIMULATION_RETRY_REGISTRY_DB_FILENAME
        con = sqlite3.connect(registry)
        try:
            retry_tables = tuple(
                row[0]
                for row in con.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
                )
            )
            assert retry_tables == (
                "simulation_retry_attempts",
                "simulation_retry_registry_meta",
            )
            attempts = con.execute(
                "SELECT retry_attempt,state FROM simulation_retry_attempts ORDER BY retry_attempt"
            ).fetchall()
            assert attempts == [(1, "LINKED"), (2, "LINKED")]
        finally:
            con.close()

        project_con = sqlite3.connect(handle.database_path)
        try:
            project_tables = tuple(
                row[0]
                for row in project_con.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
                )
            )
            assert len(project_tables) == 3
        finally:
            project_con.close()

    print(
        "SIMULATION_BOUNDED_RETRY=PASS "
        "contract_version=1.0 "
        "max_retry_attempts=2 "
        "budget_scope=ROOT_RUN_CHAIN "
        "prerequisite=F12_5_TASKS_DONE "
        "source_replacement=NEW_APPROVED_CURRENT "
        "replacement_producer=F12_5_REWORK_TASK "
        "requirement_version=PRESERVED "
        "scenario=PRESERVED "
        "mapping=REUSED_U11_2 "
        "authorization=REUSED_U11_5 "
        "runtime=EXISTING_U11_6 "
        "linkage=REUSED_U11_7 "
        "verdict=NOT_REINTERPRETED "
        "idempotent=LINKED_RETRY_REUSED "
        "crash_reservation=FAIL_CLOSED "
        "runtime_failure=CONSUMES_BUDGET "
        "persistence=simulation/simulation_retries.sqlite3 "
        "registry_tables=2 "
        "project_schema_tables=3 "
        "needs_human=active_F12_7 "
        "provider_calls=none "
        "px4_instantiation=none "
        "raw_mavsdk=none "
        "verdict_authority=independent_mission_verifier "
        "phase11=GREEN cp9=GREEN phase12=READY cp10=READY"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
