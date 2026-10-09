from __future__ import annotations

import json
import os
from pathlib import Path
import sqlite3

import pytest

from ai.artifacts import ArtifactVersionInput, ArtifactVersionStore
from ai.contracts.provenance import ProvenanceClass
from ai.project import (
    ProjectStore,
    RequirementSetStatus,
    RequirementVersionInput,
    RequirementVersionStore,
)
from ai.uav_bridge import (
    UAV_RUN_LINK_CONTRACT_VERSION,
    UAV_RUN_LINK_REGISTRY_DB_FILENAME,
    UAVRunLinkConflictError,
    UAVRunLinkStore,
    link_uav_run_to_project,
    parse_simulation_proposal,
)
from orchestration.mission_experiment import MissionExperimentResult
from src.evidence.attempt import AttemptEvidence
from src.runtime_paths import runs_root


def _mission_payload():
    return {
        "mission_id": "MISSION-001",
        "frame": "LOCAL_NED",
        "continuity_tolerance_m": 1.0,
        "terminal_home_position_m": [0.0, 0.0],
        "terminal_home_tolerance_m": 1.0,
        "steps": [
            {
                "step_id": "takeoff",
                "capability_id": "TAKEOFF-001",
                "post_action": "hold",
                "input": {
                    "task_id": "TAKEOFF-001",
                    "frame": "LOCAL_NED",
                    "target_altitude_m": 3.0,
                    "altitude_tolerance_m": 0.5,
                    "horizontal_tolerance_m": 1.0,
                    "timeout_s": 30.0,
                },
            },
            {
                "step_id": "waypoint_01",
                "capability_id": "NAV-001",
                "post_action": "hold",
                "input": {
                    "task_id": "NAV-001",
                    "frame": "LOCAL_NED",
                    "target_position_m": [5.0, 0.0, -3.0],
                    "position_tolerance_m": 0.5,
                    "timeout_s": 30.0,
                },
            },
            {
                "step_id": "return_home",
                "capability_id": "RETURN-HOME-001",
                "post_action": "hold",
                "input": {
                    "task_id": "RETURN-HOME-001",
                    "frame": "LOCAL_NED",
                    "home_position_m": [0.0, 0.0],
                    "return_altitude_m": 3.0,
                    "horizontal_tolerance_m": 0.5,
                    "altitude_tolerance_m": 0.5,
                    "timeout_s": 30.0,
                },
            },
            {
                "step_id": "land",
                "capability_id": "LAND-001",
                "post_action": "none",
                "input": {
                    "task_id": "LAND-001",
                    "frame": "LOCAL_NED",
                    "ground_altitude_tolerance_m": 0.5,
                    "horizontal_tolerance_m": 1.0,
                    "timeout_s": 30.0,
                },
            },
        ],
    }


def _make_project(tmp_path: Path):
    projects = ProjectStore(tmp_path / "state" / "projects")
    handle = projects.create_project()
    RequirementVersionStore(projects).create_initial(
        handle.project_id,
        (RequirementVersionInput(text="Run a simulation mission."),),
        status=RequirementSetStatus.APPROVED,
    )
    artifact = ArtifactVersionStore(projects).create_artifact(
        handle.project_id,
        ArtifactVersionInput(
            type="engineering_task_result.v1",
            producer_task="TASK-000001",
            input_dependency_versions=(),
            content=b"u11.7 source",
            created_timestamp="2026-10-06T11:00:00Z",
        ),
    )
    return projects, handle, artifact


def _proposal(handle, artifact, *, scenario_id: str = "U11-7-TEST"):
    return parse_simulation_proposal(
        {
            "project_id": handle.project_id.value,
            "requirement_version": 1,
            "source_artifacts": [
                {
                    "entity_id": artifact.artifact_id.value,
                    "version": artifact.version,
                }
            ],
            "scenario_id": scenario_id,
            "execution_scope": "SIMULATION_ONLY",
            "mission_id": "MISSION-001",
            "mission_payload": _mission_payload(),
        }
    )


def _runtime_result(
    deployment: Path,
    proposal,
    *,
    run_id: str = "MISSION001-U11-7-TEST",
    overall: str = "PASS",
):
    root = runs_root(deployment)
    evidence = AttemptEvidence(
        root=root,
        task_id=proposal.mission_id,
        scenario_id=proposal.scenario_id,
        attempt=1,
    )
    attempt = evidence.create()
    steps = (
        {
            "sequence": 1,
            "step_id": "takeoff",
            "capability_id": "TAKEOFF-001",
            "scenario_id": f"{proposal.scenario_id}__01_takeoff",
            "overall": overall,
            "attempt_relpath": "child",
            "finalized_sha256": "0" * 64,
            "verification_checks": {"child": overall},
            "post_action_error": None,
            "runner_error": None,
        },
    )
    verification = {
        "overall": overall,
        "checks": {"independent_mission_verifier": overall},
    }
    evidence.write_metadata(
        {
            "run_id": run_id,
            "mission_id": proposal.mission_id,
            "scope": "PX4_SITL_SIMULATION_ONLY",
            "verdict_authority": "independent_python_mission_verifier",
        }
    )
    evidence.write_json(
        "mission_input.json",
        proposal.to_dict()["mission_payload"],
    )
    evidence.write_json("mission_steps.json", {"steps": list(steps)})
    evidence.write_json("verification.json", verification)
    evidence.finalize()
    return MissionExperimentResult(
        run_id=run_id,
        mission_id=proposal.mission_id,
        attempt_dir=attempt,
        step_results=steps,
        verification=verification,
        adapter_cleanup_error=None,
    )


@pytest.fixture(autouse=True)
def legacy_runs_root(monkeypatch):
    monkeypatch.delenv("UAV_DATA_DIR", raising=False)


def test_contract_version_is_frozen():
    assert UAV_RUN_LINK_CONTRACT_VERSION == "1.0"


def test_finalized_runtime_run_links_to_exact_project_lineage(tmp_path: Path):
    projects, handle, artifact = _make_project(tmp_path)
    deployment = tmp_path / "deployment"
    deployment.mkdir()
    proposal = _proposal(handle, artifact)
    result = _runtime_result(deployment, proposal)

    record = link_uav_run_to_project(
        projects,
        deployment,
        proposal,
        result,
    )

    assert record.project_id == handle.project_id
    assert record.requirement_version == 1
    assert record.source_artifacts == proposal.source_artifacts
    assert record.scenario_id == proposal.scenario_id
    assert record.mission_id == "MISSION-001"
    assert record.runtime_run_id == result.run_id
    assert record.attempt_relpath == "MISSION-001/U11-7-TEST/attempt_001"
    assert record.simulation_provenance is ProvenanceClass.SIMULATION_RESULT
    assert record.verifier_provenance is ProvenanceClass.VERIFIER_RESULT

    raw = record.to_dict()
    assert "overall" not in raw
    assert "issues" not in raw
    assert "rework" not in raw


def test_link_accepts_fail_verdict_without_reinterpreting_it(tmp_path: Path):
    projects, handle, artifact = _make_project(tmp_path)
    deployment = tmp_path / "deployment"
    deployment.mkdir()
    proposal = _proposal(handle, artifact, scenario_id="U11-7-FAIL")
    result = _runtime_result(
        deployment,
        proposal,
        run_id="MISSION001-U11-7-FAIL",
        overall="FAIL",
    )

    record = UAVRunLinkStore(projects, deployment).link(proposal, result)

    assert record.runtime_run_id == result.run_id
    assert json.loads(
        (result.attempt_dir / "verification.json").read_text(encoding="utf-8")
    )["overall"] == "FAIL"
    assert "overall" not in record.to_dict()


def test_link_is_idempotent_and_restart_safe(tmp_path: Path):
    projects, handle, artifact = _make_project(tmp_path)
    deployment = tmp_path / "deployment"
    deployment.mkdir()
    proposal = _proposal(handle, artifact)
    result = _runtime_result(deployment, proposal)

    first = UAVRunLinkStore(projects, deployment).link(proposal, result)
    second = UAVRunLinkStore(projects, deployment).link(proposal, result)
    restarted = UAVRunLinkStore(
        ProjectStore(projects.projects_root),
        deployment,
    ).open(handle.project_id, result.run_id)

    assert second == first
    assert restarted == first


def test_registry_is_project_local_and_has_exactly_two_tables(tmp_path: Path):
    projects, handle, artifact = _make_project(tmp_path)
    deployment = tmp_path / "deployment"
    deployment.mkdir()
    proposal = _proposal(handle, artifact)
    result = _runtime_result(deployment, proposal)

    UAVRunLinkStore(projects, deployment).link(proposal, result)

    registry = (
        handle.layout.data_directories["simulation"]
        / UAV_RUN_LINK_REGISTRY_DB_FILENAME
    )
    assert registry.is_file()
    with sqlite3.connect(registry) as connection:
        tables = tuple(
            row[0]
            for row in connection.execute(
                """
                SELECT name FROM sqlite_master
                WHERE type='table' AND name NOT LIKE 'sqlite_%'
                ORDER BY name
                """
            )
        )
    assert tables == (
        "uav_run_link_registry_meta",
        "uav_run_links",
    )


def test_missing_project_lineage_is_rejected(tmp_path: Path):
    projects, handle, artifact = _make_project(tmp_path)
    deployment = tmp_path / "deployment"
    deployment.mkdir()
    raw = _proposal(handle, artifact).to_dict()
    raw["source_artifacts"] = [
        {"entity_id": "ART-999999", "version": 1}
    ]
    proposal = parse_simulation_proposal(raw)
    result = _runtime_result(deployment, proposal)

    with pytest.raises(UAVRunLinkConflictError, match="source artifact"):
        UAVRunLinkStore(projects, deployment).link(proposal, result)


def test_unfinalized_or_tampered_evidence_is_rejected(tmp_path: Path):
    projects, handle, artifact = _make_project(tmp_path)
    deployment = tmp_path / "deployment"
    deployment.mkdir()
    proposal = _proposal(handle, artifact)
    result = _runtime_result(deployment, proposal)

    verification = result.attempt_dir / "verification.json"
    verification.chmod(0o600)
    verification.write_text(
        '{"checks":{},"overall":"PASS"}\n',
        encoding="utf-8",
    )

    with pytest.raises(UAVRunLinkConflictError, match="not finalized and intact"):
        UAVRunLinkStore(projects, deployment).link(proposal, result)


def test_runtime_result_must_match_evidence_and_proposal(tmp_path: Path):
    projects, handle, artifact = _make_project(tmp_path)
    deployment = tmp_path / "deployment"
    deployment.mkdir()
    proposal = _proposal(handle, artifact)
    result = _runtime_result(deployment, proposal)

    mismatched = MissionExperimentResult(
        run_id=result.run_id,
        mission_id="FUTURE-MISSION-777",
        attempt_dir=result.attempt_dir,
        step_results=result.step_results,
        verification=result.verification,
        adapter_cleanup_error=None,
    )
    with pytest.raises(UAVRunLinkConflictError, match="mission_id"):
        UAVRunLinkStore(projects, deployment).link(proposal, mismatched)


def test_attempt_must_remain_inside_existing_runs_root(tmp_path: Path):
    projects, handle, artifact = _make_project(tmp_path)
    deployment = tmp_path / "deployment"
    deployment.mkdir()
    proposal = _proposal(handle, artifact)
    result = _runtime_result(deployment, proposal)
    escaped = MissionExperimentResult(
        run_id=result.run_id,
        mission_id=result.mission_id,
        attempt_dir=tmp_path / "outside" / "attempt_001",
        step_results=result.step_results,
        verification=result.verification,
        adapter_cleanup_error=None,
    )

    with pytest.raises(UAVRunLinkConflictError, match="outside"):
        UAVRunLinkStore(projects, deployment).link(proposal, escaped)


def test_same_runtime_run_id_cannot_bind_conflicting_project_lineage(tmp_path: Path):
    projects, handle, artifact = _make_project(tmp_path)
    deployment = tmp_path / "deployment"
    deployment.mkdir()

    first_proposal = _proposal(handle, artifact, scenario_id="U11-7-A")
    first_result = _runtime_result(
        deployment,
        first_proposal,
        run_id="MISSION001-U11-7-CONFLICT",
    )
    store = UAVRunLinkStore(projects, deployment)
    store.link(first_proposal, first_result)

    second_proposal = _proposal(handle, artifact, scenario_id="U11-7-B")
    second_result = _runtime_result(
        deployment,
        second_proposal,
        run_id="MISSION001-U11-7-CONFLICT",
    )
    with pytest.raises(UAVRunLinkConflictError, match="conflicting"):
        store.link(second_proposal, second_result)
