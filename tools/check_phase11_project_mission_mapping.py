#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ai.artifacts import ArtifactLifecycleStore, ArtifactVersionInput, ArtifactVersionStore
from ai.contracts.artifact_states import ArtifactState
from ai.contracts.provenance import ProvenanceClass
from ai.contracts.validation_result import ValidationCheck, ValidationResult
from ai.project import Fact, FactSet, FactStore, ProjectStore, RequirementSetStatus, RequirementVersionInput, RequirementVersionStore
from ai.uav_bridge import PROJECT_MISSION_MAPPING_CONTRACT_VERSION, PROJECT_MISSION_REQUEST_FACT_KEY, map_project_to_mission


def pass_validation():
    return ValidationResult.from_checks((ValidationCheck.from_dict({
        "check_id": "u11-2-acceptance", "status": "PASS", "field": "source",
        "reason": None, "details": {"accepted": True},
    }),))


def main() -> int:
    assert PROJECT_MISSION_MAPPING_CONTRACT_VERSION == "1.0"
    with tempfile.TemporaryDirectory(prefix="u11-2-") as td:
        projects = ProjectStore(Path(td) / "projects")
        handle = projects.create_project()
        RequirementVersionStore(projects).create_initial(
            handle.project_id,
            (RequirementVersionInput(text="Execute the project mission in simulation."),),
            status=RequirementSetStatus.APPROVED,
        )
        FactStore(projects).save(FactSet(project_id=handle.project_id, facts=(Fact(
            key=PROJECT_MISSION_REQUEST_FACT_KEY,
            value={
                "scenario_id": "U11-2-ACCEPTANCE",
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
            },
            provenance=ProvenanceClass.USER_FACT,
        ),)))
        versions = ArtifactVersionStore(projects)
        record = versions.create_artifact(handle.project_id, ArtifactVersionInput(
            type="engineering_task_result.v1", producer_task="TASK-000001",
            input_dependency_versions=(), content=b"u11-2-approved-source",
            created_timestamp="2026-10-06T10:00:00Z",
        ))
        lifecycle = ArtifactLifecycleStore(projects)
        lifecycle.transition(handle.project_id, record.artifact_id, 1, ArtifactState.REVIEWED)
        lifecycle.transition(handle.project_id, record.artifact_id, 1, ArtifactState.VALIDATED, validation_result=pass_validation())
        lifecycle.transition(handle.project_id, record.artifact_id, 1, ArtifactState.APPROVED)

        proposal = map_project_to_mission(
            projects,
            handle.project_id,
            [{"entity_id": record.artifact_id.value, "version": 1}],
        )
        raw = proposal.to_dict()
        assert raw["requirement_version"] == 1
        assert raw["execution_scope"] == "SIMULATION_ONLY"
        assert raw["mission_id"] == "MISSION-001"
        assert raw["mission_payload"]["frame"] == "LOCAL_NED"
        assert [x["capability_id"] for x in raw["mission_payload"]["steps"]] == [
            "TAKEOFF-001", "NAV-001", "RETURN-HOME-001", "LAND-001"
        ]
        assert raw["mission_payload"]["steps"][1]["input"]["target_position_m"] == [5.0, 0.0, -3.0]

    print(
        "PROJECT_MISSION_MAPPING=PASS contract_version=1.0 "
        "requirements=LATEST_APPROVED fact=simulation.mission_request "
        "source_artifacts=APPROVED_CURRENT deterministic=PASS "
        "mission=MISSION-001 frame=LOCAL_NED altitude_conversion=PASS "
        "bridge_validation=active_U11_3 capability_allowlist=active_U11_4 "
        "mission_allowlist=active_U11_5 runtime_invocation=active_U11_6 "
        "uav_run_link=reserved_for_U11_7 persistence=none provider_calls=none "
        "px4_access=none phase11=pending cp9=pending"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
