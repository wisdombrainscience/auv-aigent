from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from ai.artifacts import ArtifactVersionInput, ArtifactVersionStore
from ai.contracts.provenance import ProvenanceClass
from ai.project import (
    ProjectStore,
    RequirementSetStatus,
    RequirementVersionInput,
    RequirementVersionStore,
)
from ai.simulation_feedback import (
    MISSION_VERIFIER_CHECK_IDS,
    VERIFIER_RESULT_PARSER_CONTRACT_VERSION,
    VerifierResultEvidenceError,
    VerifierResultSchemaError,
    parse_linked_verifier_result,
)
from ai.uav_bridge import link_uav_run_to_project, parse_simulation_proposal
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
        (RequirementVersionInput(text="Parse linked verifier evidence."),),
        status=RequirementSetStatus.APPROVED,
    )
    artifact = ArtifactVersionStore(projects).create_artifact(
        handle.project_id,
        ArtifactVersionInput(
            type="engineering_task_result.v1",
            producer_task="TASK-000001",
            input_dependency_versions=(),
            content=b"f12.1 source",
            created_timestamp="2026-10-06T13:00:00Z",
        ),
    )
    proposal = parse_simulation_proposal(
        {
            "project_id": handle.project_id.value,
            "requirement_version": 1,
            "source_artifacts": [
                {
                    "entity_id": artifact.artifact_id.value,
                    "version": artifact.version,
                }
            ],
            "scenario_id": "F12-1-PARSER",
            "execution_scope": "SIMULATION_ONLY",
            "mission_id": "MISSION-001",
            "mission_payload": _mission_payload(),
        }
    )
    return projects, handle, artifact, proposal


def _verification(*, overall: str = "PASS", malformed: bool = False):
    checks = {check_id: "PASS" for check_id in MISSION_VERIFIER_CHECK_IDS}
    failed_step_ids: list[str] = []
    reason = None
    max_transition = 0.25
    terminal_home = 0.1
    if overall == "FAIL":
        checks["continuity"] = "FAIL"
        checks["terminal_home"] = "FAIL"
        failed_step_ids = ["land"]
        reason = "terminal home condition failed"
        terminal_home = 4.2
    payload = {
        "overall": overall,
        "checks": checks,
        "max_transition_distance_m": max_transition,
        "terminal_home_distance_m": terminal_home,
        "failed_step_ids": failed_step_ids,
        "reason": reason,
    }
    if malformed:
        payload.pop("reason")
    return payload


def _linked_result(
    deployment: Path,
    projects: ProjectStore,
    proposal,
    *,
    run_id: str = "MISSION001-F12-1-PASS",
    overall: str = "PASS",
    malformed: bool = False,
):
    root = runs_root(deployment)
    evidence = AttemptEvidence(
        root=root,
        task_id=proposal.mission_id,
        scenario_id=proposal.scenario_id,
        attempt=1,
    )
    attempt = evidence.create()
    verification = _verification(overall=overall, malformed=malformed)
    steps = ()
    evidence.write_metadata(
        {
            "run_id": run_id,
            "mission_id": proposal.mission_id,
            "scope": "PX4_SITL_SIMULATION_ONLY",
            "verdict_authority": "independent_python_mission_verifier",
        }
    )
    evidence.write_json("mission_input.json", proposal.to_dict()["mission_payload"])
    evidence.write_json("mission_steps.json", {"steps": []})
    evidence.write_json("verification.json", verification)
    evidence.finalize()
    result = MissionExperimentResult(
        run_id=run_id,
        mission_id=proposal.mission_id,
        attempt_dir=attempt,
        step_results=steps,
        verification=verification,
        adapter_cleanup_error=None,
    )
    link = link_uav_run_to_project(projects, deployment, proposal, result)
    return link, result


@pytest.fixture(autouse=True)
def legacy_runs_root(monkeypatch):
    monkeypatch.delenv("UAV_DATA_DIR", raising=False)


def test_contract_version_is_frozen():
    assert VERIFIER_RESULT_PARSER_CONTRACT_VERSION == "1.0"


def test_parser_reads_hash_bound_pass_result_without_reinterpreting(tmp_path: Path):
    projects, handle, artifact, proposal = _make_project(tmp_path)
    deployment = tmp_path / "deployment"
    deployment.mkdir()
    link, _ = _linked_result(deployment, projects, proposal)

    parsed = parse_linked_verifier_result(
        projects,
        deployment,
        handle.project_id,
        link.runtime_run_id,
    )

    assert parsed.project_id == handle.project_id
    assert parsed.requirement_version == 1
    assert parsed.source_artifacts == proposal.source_artifacts
    assert parsed.runtime_run_id == link.runtime_run_id
    assert parsed.overall == "PASS"
    assert parsed.failed_checks == ()
    assert parsed.failed_step_ids == ()
    assert parsed.reason is None
    assert parsed.verification_sha256 == link.verification_sha256
    assert parsed.provenance is ProvenanceClass.VERIFIER_RESULT
    raw = parsed.to_dict()
    assert raw["contract_version"] == "1.0"
    assert "issue" not in raw
    assert "owner" not in raw
    assert "rework" not in raw
    assert "retry" not in raw


def test_parser_preserves_fail_verdict_and_derives_failed_checks_only(tmp_path: Path):
    projects, handle, _, proposal = _make_project(tmp_path)
    deployment = tmp_path / "deployment"
    deployment.mkdir()
    link, _ = _linked_result(
        deployment,
        projects,
        proposal,
        run_id="MISSION001-F12-1-FAIL",
        overall="FAIL",
    )

    parsed = parse_linked_verifier_result(
        projects, deployment, handle.project_id, link.runtime_run_id
    )

    assert parsed.overall == "FAIL"
    assert parsed.failed_checks == ("continuity", "terminal_home")
    assert parsed.failed_step_ids == ("land",)
    assert parsed.reason == "terminal home condition failed"
    assert parsed.terminal_home_distance_m == 4.2
    assert parsed.provenance is ProvenanceClass.VERIFIER_RESULT


def test_parser_requires_existing_u11_7_link(tmp_path: Path):
    projects, handle, _, _ = _make_project(tmp_path)
    deployment = tmp_path / "deployment"
    deployment.mkdir()
    with pytest.raises(VerifierResultEvidenceError, match="U11.7 run linkage"):
        parse_linked_verifier_result(
            projects,
            deployment,
            handle.project_id,
            "MISSION001-NOT-LINKED",
        )


def test_parser_rejects_verification_tamper_after_link(tmp_path: Path):
    projects, handle, _, proposal = _make_project(tmp_path)
    deployment = tmp_path / "deployment"
    deployment.mkdir()
    link, result = _linked_result(deployment, projects, proposal)
    verification = result.attempt_dir / "verification.json"
    verification.chmod(0o644)
    verification.write_text("{}\n", encoding="utf-8")

    with pytest.raises(VerifierResultEvidenceError, match="integrity failed"):
        parse_linked_verifier_result(
            projects, deployment, handle.project_id, link.runtime_run_id
        )


def test_parser_rejects_unexpected_post_finalize_evidence(tmp_path: Path):
    projects, handle, _, proposal = _make_project(tmp_path)
    deployment = tmp_path / "deployment"
    deployment.mkdir()
    link, result = _linked_result(deployment, projects, proposal)
    extra = result.attempt_dir / "unexpected.json"
    extra.write_text("{}\n", encoding="utf-8")

    with pytest.raises(VerifierResultEvidenceError, match="integrity failed"):
        parse_linked_verifier_result(
            projects, deployment, handle.project_id, link.runtime_run_id
        )


def test_parser_rejects_malformed_verifier_schema_even_when_hash_bound(tmp_path: Path):
    projects, handle, _, proposal = _make_project(tmp_path)
    deployment = tmp_path / "deployment"
    deployment.mkdir()
    link, _ = _linked_result(
        deployment,
        projects,
        proposal,
        run_id="MISSION001-F12-1-MALFORMED",
        malformed=True,
    )

    with pytest.raises(VerifierResultSchemaError, match="keys do not match"):
        parse_linked_verifier_result(
            projects, deployment, handle.project_id, link.runtime_run_id
        )


def test_parser_rejects_inconsistent_overall(tmp_path: Path):
    projects, handle, _, proposal = _make_project(tmp_path)
    deployment = tmp_path / "deployment"
    deployment.mkdir()
    root = runs_root(deployment)
    evidence = AttemptEvidence(
        root=root,
        task_id=proposal.mission_id,
        scenario_id=proposal.scenario_id,
        attempt=1,
    )
    attempt = evidence.create()
    verification = _verification(overall="PASS")
    verification["checks"]["continuity"] = "FAIL"
    evidence.write_metadata(
        {
            "run_id": "MISSION001-F12-1-INCONSISTENT",
            "mission_id": proposal.mission_id,
            "scope": "PX4_SITL_SIMULATION_ONLY",
            "verdict_authority": "independent_python_mission_verifier",
        }
    )
    evidence.write_json("mission_input.json", proposal.to_dict()["mission_payload"])
    evidence.write_json("mission_steps.json", {"steps": []})
    evidence.write_json("verification.json", verification)
    evidence.finalize()
    result = MissionExperimentResult(
        run_id="MISSION001-F12-1-INCONSISTENT",
        mission_id=proposal.mission_id,
        attempt_dir=attempt,
        step_results=(),
        verification=verification,
        adapter_cleanup_error=None,
    )
    link = link_uav_run_to_project(projects, deployment, proposal, result)

    with pytest.raises(VerifierResultSchemaError, match="overall does not match"):
        parse_linked_verifier_result(
            projects, deployment, handle.project_id, link.runtime_run_id
        )


def test_parser_is_read_only_for_project_and_link_registries(tmp_path: Path):
    projects, handle, _, proposal = _make_project(tmp_path)
    deployment = tmp_path / "deployment"
    deployment.mkdir()
    link, _ = _linked_result(deployment, projects, proposal)
    before = {
        p.relative_to(handle.layout.project_dir).as_posix(): p.stat().st_mtime_ns
        for p in handle.layout.project_dir.rglob("*")
        if p.is_file()
    }
    parse_linked_verifier_result(
        projects, deployment, handle.project_id, link.runtime_run_id
    )
    after = {
        p.relative_to(handle.layout.project_dir).as_posix(): p.stat().st_mtime_ns
        for p in handle.layout.project_dir.rglob("*")
        if p.is_file()
    }
    assert before == after
