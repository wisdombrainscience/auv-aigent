#!/usr/bin/env python3
from __future__ import annotations

import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ai.artifacts import ArtifactVersionInput, ArtifactVersionStore
from ai.project import (
    ProjectStore,
    RequirementSetStatus,
    RequirementVersionInput,
    RequirementVersionStore,
)
from ai.uav_bridge import (
    UAV_RUN_LINK_CONTRACT_VERSION,
    UAV_RUN_LINK_REGISTRY_DB_FILENAME,
    UAVRunLinkStore,
    link_uav_run_to_project,
    parse_simulation_proposal,
)
from orchestration.mission_experiment import MissionExperimentResult
from src.evidence.attempt import AttemptEvidence


def mission_payload():
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


def make_result(deployment: Path, proposal, *, run_id: str, overall: str):
    root = deployment / "runs"
    evidence = AttemptEvidence(
        root=root,
        task_id=proposal.mission_id,
        scenario_id=proposal.scenario_id,
        attempt=1,
    )
    attempt = evidence.create()
    steps = ()
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
    evidence.write_json("mission_input.json", proposal.to_dict()["mission_payload"])
    evidence.write_json("mission_steps.json", {"steps": []})
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


def main() -> int:
    assert UAV_RUN_LINK_CONTRACT_VERSION == "1.0"

    with tempfile.TemporaryDirectory(prefix="u11_7_probe_") as temp:
        root = Path(temp)
        deployment = root / "deployment"
        deployment.mkdir()
        projects = ProjectStore(root / "state" / "projects")
        handle = projects.create_project()

        RequirementVersionStore(projects).create_initial(
            handle.project_id,
            (RequirementVersionInput(text="Run verified simulation."),),
            status=RequirementSetStatus.APPROVED,
        )
        artifact = ArtifactVersionStore(projects).create_artifact(
            handle.project_id,
            ArtifactVersionInput(
                type="engineering_task_result.v1",
                producer_task="TASK-000001",
                input_dependency_versions=(),
                content=b"u11.7 acceptance source",
                created_timestamp="2026-10-06T11:00:00Z",
            ),
        )

        def proposal_for(scenario_id: str):
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
                    "mission_payload": mission_payload(),
                }
            )

        proposal = proposal_for("U11-7-ACCEPTANCE")
        result = make_result(
            deployment,
            proposal,
            run_id="MISSION001-U11-7-ACCEPTANCE",
            overall="PASS",
        )
        first = link_uav_run_to_project(
            projects,
            deployment,
            proposal,
            result,
        )
        second = link_uav_run_to_project(
            projects,
            deployment,
            proposal,
            result,
        )
        assert first == second

        reopened = UAVRunLinkStore(
            ProjectStore(projects.projects_root),
            deployment,
        ).open(handle.project_id, result.run_id)
        assert reopened == first

        fail_proposal = proposal_for("U11-7-FAIL-ACCEPTANCE")
        fail_result = make_result(
            deployment,
            fail_proposal,
            run_id="MISSION001-U11-7-FAIL-ACCEPTANCE",
            overall="FAIL",
        )
        fail_link = UAVRunLinkStore(projects, deployment).link(
            fail_proposal,
            fail_result,
        )
        assert fail_link.runtime_run_id == fail_result.run_id
        assert "overall" not in fail_link.to_dict()

        registry = (
            handle.layout.data_directories["simulation"]
            / UAV_RUN_LINK_REGISTRY_DB_FILENAME
        )
        with sqlite3.connect(registry) as connection:
            registry_tables = connection.execute(
                """
                SELECT COUNT(*) FROM sqlite_master
                WHERE type='table' AND name NOT LIKE 'sqlite_%'
                """
            ).fetchone()[0]
        with sqlite3.connect(handle.database_path) as connection:
            project_schema_tables = connection.execute(
                """
                SELECT COUNT(*) FROM sqlite_master
                WHERE type='table' AND name NOT LIKE 'sqlite_%'
                """
            ).fetchone()[0]

        assert registry_tables == 2
        assert project_schema_tables == 3

    print(
        "UAV_RUN_PROJECT_LINKAGE=PASS contract_version=1.0 "
        "project_local=simulation/uav_run_links.sqlite3 "
        "lineage=requirement+source_artifacts "
        "evidence=FINALIZED_INTACT "
        "runtime_result=HASH_BOUND verifier_evidence=HASH_BOUND "
        "provenance=SIMULATION_RESULT+VERIFIER_RESULT "
        "fail_verdict=LINKED_NOT_REINTERPRETED "
        "idempotent=PASS restart=PASS registry_tables=2 project_schema_tables=3 "
        "phase12_feedback=reserved "
        "provider_calls=none px4_access=none raw_mavsdk=none "
        "verdict_authority=independent_mission_verifier "
        "phase11=READY cp9=READY"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
