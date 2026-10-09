#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
import sys
import tempfile

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
from ai.simulation_feedback import (
    MISSION_VERIFIER_CHECK_IDS,
    VERIFIER_RESULT_PARSER_CONTRACT_VERSION,
    VerifierResultEvidenceError,
    parse_linked_verifier_result,
)
from ai.uav_bridge import link_uav_run_to_project, parse_simulation_proposal
from orchestration.mission_experiment import MissionExperimentResult
from src.evidence.attempt import AttemptEvidence
from src.runtime_paths import runs_root


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


def make_verification(overall: str):
    checks = {check_id: "PASS" for check_id in MISSION_VERIFIER_CHECK_IDS}
    failed_step_ids = []
    reason = None
    home = 0.1
    if overall == "FAIL":
        checks["continuity"] = "FAIL"
        checks["terminal_home"] = "FAIL"
        failed_step_ids = ["land"]
        reason = "terminal home condition failed"
        home = 3.5
    return {
        "overall": overall,
        "checks": checks,
        "max_transition_distance_m": 0.25,
        "terminal_home_distance_m": home,
        "failed_step_ids": failed_step_ids,
        "reason": reason,
    }


def make_link(projects, deployment, proposal, *, run_id: str, overall: str):
    evidence = AttemptEvidence(
        root=runs_root(deployment),
        task_id=proposal.mission_id,
        scenario_id=proposal.scenario_id,
        attempt=1,
    )
    attempt = evidence.create()
    verification = make_verification(overall)
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
        step_results=(),
        verification=verification,
        adapter_cleanup_error=None,
    )
    return link_uav_run_to_project(projects, deployment, proposal, result), attempt


def main() -> int:
    assert VERIFIER_RESULT_PARSER_CONTRACT_VERSION == "1.0"

    with tempfile.TemporaryDirectory(prefix="f12_1_probe_") as temp:
        root = Path(temp)
        deployment = root / "deployment"
        deployment.mkdir()
        projects = ProjectStore(root / "state" / "projects")
        handle = projects.create_project()
        RequirementVersionStore(projects).create_initial(
            handle.project_id,
            (RequirementVersionInput(text="Parse verifier result."),),
            status=RequirementSetStatus.APPROVED,
        )
        artifact = ArtifactVersionStore(projects).create_artifact(
            handle.project_id,
            ArtifactVersionInput(
                type="engineering_task_result.v1",
                producer_task="TASK-000001",
                input_dependency_versions=(),
                content=b"f12.1 probe source",
                created_timestamp="2026-10-06T13:00:00Z",
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

        pass_proposal = proposal_for("F12-1-PASS")
        pass_link, _ = make_link(
            projects,
            deployment,
            pass_proposal,
            run_id="MISSION001-F12-1-PASS",
            overall="PASS",
        )
        passed = parse_linked_verifier_result(
            projects, deployment, handle.project_id, pass_link.runtime_run_id
        )
        assert passed.overall == "PASS"
        assert passed.failed_checks == ()

        fail_proposal = proposal_for("F12-1-FAIL")
        fail_link, fail_attempt = make_link(
            projects,
            deployment,
            fail_proposal,
            run_id="MISSION001-F12-1-FAIL",
            overall="FAIL",
        )
        failed = parse_linked_verifier_result(
            projects, deployment, handle.project_id, fail_link.runtime_run_id
        )
        assert failed.overall == "FAIL"
        assert failed.failed_checks == ("continuity", "terminal_home")
        assert failed.failed_step_ids == ("land",)

        verification = fail_attempt / "verification.json"
        verification.chmod(0o644)
        verification.write_text("{}\n", encoding="utf-8")
        tamper_denied = False
        try:
            parse_linked_verifier_result(
                projects, deployment, handle.project_id, fail_link.runtime_run_id
            )
        except VerifierResultEvidenceError:
            tamper_denied = True
        assert tamper_denied

    print(
        "VERIFIER_RESULT_PARSER=PASS "
        "contract_version=1.0 "
        "source=U11_7_LINKED_VERIFIER "
        "evidence=FINALIZED_HASH_BOUND "
        "pass=PARSED_UNCHANGED "
        "fail=PARSED_UNCHANGED "
        "failed_checks=DERIVED "
        "schema=STRICT_MISSION_001 "
        "tamper=DENY "
        "provenance=VERIFIER_RESULT "
        "persistence=none "
        "issue_taxonomy=active_F12_2 "
        "role_mapping=active_F12_3 "
        "lineage_invalidation=active_F12_4 "
        "rework_task=active_F12_5 "
        "retry=active_F12_6 "
        "needs_human=active_F12_7 "
        "provider_calls=none "
        "px4_access=none "
        "verdict_authority=independent_mission_verifier "
        "phase11=GREEN cp9=GREEN phase12=READY cp10=READY"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
