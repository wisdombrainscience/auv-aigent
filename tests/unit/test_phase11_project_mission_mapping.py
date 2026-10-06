from __future__ import annotations

import math
from pathlib import Path

import pytest

from ai.artifacts import ArtifactLifecycleStore, ArtifactVersionInput, ArtifactVersionStore
from ai.contracts.artifact_states import ArtifactState
from ai.contracts.validation_result import ValidationCheck, ValidationResult
from ai.project import (
    Fact,
    FactSet,
    FactStore,
    ProjectStore,
    RequirementSetStatus,
    RequirementVersionInput,
    RequirementVersionStore,
)
from ai.contracts.provenance import ProvenanceClass
from ai.uav_bridge import (
    MAPPED_MISSION_FRAME,
    MAPPED_MISSION_ID,
    PROJECT_MISSION_MAPPING_CONTRACT_VERSION,
    PROJECT_MISSION_REQUEST_FACT_KEY,
    ProjectMissionMappingError,
    map_project_to_mission,
)


def mission_request(**overrides):
    raw = {
        "scenario_id": "U11-2-MAP",
        "home_position_m": [1.0, -2.0],
        "waypoints": [
            {"north": 5.0, "east": 0.0, "altitude": 3.0},
            {"north": 7.5, "east": -1.0, "altitude": 4.0},
        ],
        "takeoff_altitude_m": 3.0,
        "return_altitude_m": 3.5,
        "position_tolerance_m": 0.5,
        "altitude_tolerance_m": 0.5,
        "horizontal_tolerance_m": 1.0,
        "ground_altitude_tolerance_m": 0.5,
        "continuity_tolerance_m": 1.0,
        "terminal_home_tolerance_m": 1.0,
        "timeout_s": 30.0,
    }
    raw.update(overrides)
    return raw


def pass_validation() -> ValidationResult:
    return ValidationResult.from_checks(
        (
            ValidationCheck.from_dict(
                {
                    "check_id": "u11-2-source",
                    "status": "PASS",
                    "field": "source",
                    "reason": None,
                    "details": {"accepted": True},
                }
            ),
        )
    )


def make_project(tmp_path: Path, *, requirement_status=RequirementSetStatus.APPROVED):
    projects = ProjectStore(tmp_path / "projects")
    handle = projects.create_project()
    RequirementVersionStore(projects).create_initial(
        handle.project_id,
        (RequirementVersionInput(text="Fly a simulation mission."),),
        status=requirement_status,
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
    return projects, handle


def create_artifact(projects: ProjectStore, project_id, *, content: bytes, approve: bool = True):
    versions = ArtifactVersionStore(projects)
    record = versions.create_artifact(
        project_id,
        ArtifactVersionInput(
            type="engineering_task_result.v1",
            producer_task="TASK-000001",
            input_dependency_versions=(),
            content=content,
            created_timestamp="2026-10-06T10:00:00Z",
        ),
    )
    if approve:
        lifecycle = ArtifactLifecycleStore(projects)
        lifecycle.transition(project_id, record.artifact_id, record.version, ArtifactState.REVIEWED)
        lifecycle.transition(
            project_id,
            record.artifact_id,
            record.version,
            ArtifactState.VALIDATED,
            validation_result=pass_validation(),
        )
        lifecycle.transition(project_id, record.artifact_id, record.version, ArtifactState.APPROVED)
    return record


def source_ref(record):
    return {"entity_id": record.artifact_id.value, "version": record.version}


def test_contract_and_nominal_mapping(tmp_path: Path):
    assert PROJECT_MISSION_MAPPING_CONTRACT_VERSION == "1.0"
    projects, handle = make_project(tmp_path)
    record = create_artifact(projects, handle.project_id, content=b"approved-source")

    proposal = map_project_to_mission(projects, handle.project_id, [source_ref(record)])

    assert proposal.project_id == handle.project_id
    assert proposal.requirement_version == 1
    assert proposal.scenario_id == "U11-2-MAP"
    assert proposal.mission_id == MAPPED_MISSION_ID == "MISSION-001"
    assert proposal.execution_scope.value == "SIMULATION_ONLY"

    payload = proposal.to_dict()["mission_payload"]
    assert payload["mission_id"] == "MISSION-001"
    assert payload["frame"] == MAPPED_MISSION_FRAME == "LOCAL_NED"
    assert [step["capability_id"] for step in payload["steps"]] == [
        "TAKEOFF-001",
        "NAV-001",
        "NAV-001",
        "RETURN-HOME-001",
        "LAND-001",
    ]
    assert payload["steps"][1]["input"]["target_position_m"] == [5.0, 0.0, -3.0]
    assert payload["steps"][2]["input"]["target_position_m"] == [7.5, -1.0, -4.0]
    assert payload["terminal_home_position_m"] == [1.0, -2.0]


def test_mapping_is_deterministic_and_sorts_source_artifacts(tmp_path: Path):
    projects, handle = make_project(tmp_path)
    first = create_artifact(projects, handle.project_id, content=b"a")
    second = create_artifact(projects, handle.project_id, content=b"b")

    left = map_project_to_mission(
        projects, handle.project_id, [source_ref(second), source_ref(first)]
    )
    right = map_project_to_mission(
        projects, handle.project_id, [source_ref(first), source_ref(second)]
    )
    assert left.to_json() == right.to_json()
    assert [x.entity_id.value for x in left.source_artifacts] == sorted(
        [first.artifact_id.value, second.artifact_id.value]
    )


def test_latest_requirements_must_be_approved(tmp_path: Path):
    projects, handle = make_project(tmp_path)
    record = create_artifact(projects, handle.project_id, content=b"source")
    RequirementVersionStore(projects).append_version(
        handle.project_id,
        (RequirementVersionInput(requirement_id="REQ-000001", text="Changed requirement."),),
        status=RequirementSetStatus.DRAFT,
    )
    with pytest.raises(ProjectMissionMappingError, match="latest requirement version must be APPROVED"):
        map_project_to_mission(projects, handle.project_id, [source_ref(record)])


def test_source_artifact_must_be_approved(tmp_path: Path):
    projects, handle = make_project(tmp_path)
    draft = create_artifact(projects, handle.project_id, content=b"draft", approve=False)
    with pytest.raises(ProjectMissionMappingError, match="must be APPROVED"):
        map_project_to_mission(projects, handle.project_id, [source_ref(draft)])


def test_source_artifact_must_be_current_approved_version(tmp_path: Path):
    projects, handle = make_project(tmp_path)
    versions = ArtifactVersionStore(projects)
    first = create_artifact(projects, handle.project_id, content=b"v1")
    second = versions.append_version(
        handle.project_id,
        first.artifact_id,
        ArtifactVersionInput(
            type=first.type,
            producer_task="TASK-000001",
            input_dependency_versions=(),
            content=b"v2",
            created_timestamp="2026-10-06T10:01:00Z",
        ),
    )
    lifecycle = ArtifactLifecycleStore(projects)
    lifecycle.transition(handle.project_id, second.artifact_id, second.version, ArtifactState.REVIEWED)
    lifecycle.transition(
        handle.project_id,
        second.artifact_id,
        second.version,
        ArtifactState.VALIDATED,
        validation_result=pass_validation(),
    )
    lifecycle.transition(handle.project_id, second.artifact_id, second.version, ArtifactState.APPROVED)

    with pytest.raises(ProjectMissionMappingError, match="must be APPROVED|not the current APPROVED version"):
        map_project_to_mission(projects, handle.project_id, [source_ref(first)])

    mapped = map_project_to_mission(projects, handle.project_id, [source_ref(second)])
    assert mapped.source_artifacts[0].version == 2


def test_mission_fact_is_required(tmp_path: Path):
    projects, handle = make_project(tmp_path)
    record = create_artifact(projects, handle.project_id, content=b"source")
    FactStore(projects).save(FactSet(project_id=handle.project_id, facts=()))
    with pytest.raises(ProjectMissionMappingError, match="exactly one simulation.mission_request"):
        map_project_to_mission(projects, handle.project_id, [source_ref(record)])


def test_mission_request_fields_are_exact(tmp_path: Path):
    projects, handle = make_project(tmp_path)
    record = create_artifact(projects, handle.project_id, content=b"source")
    bad = mission_request(); bad["verdict"] = "PASS"
    FactStore(projects).save(
        FactSet(
            project_id=handle.project_id,
            facts=(Fact(key=PROJECT_MISSION_REQUEST_FACT_KEY, value=bad, provenance=ProvenanceClass.USER_FACT),),
        )
    )
    with pytest.raises(ProjectMissionMappingError, match="exactly the U11.2 fields"):
        map_project_to_mission(projects, handle.project_id, [source_ref(record)])


@pytest.mark.parametrize(
    "overrides,match",
    [
        ({"waypoints": []}, "waypoints"),
        ({"timeout_s": 0.0}, "timeout_s"),
        ({"takeoff_altitude_m": True}, "numeric"),
        ({"home_position_m": [0.0]}, "home_position_m"),
        ({"waypoints": [{"north": 1.0, "east": 2.0, "altitude": -3.0}]}, "altitude"),
    ],
)
def test_invalid_mapping_inputs_fail_closed(tmp_path: Path, overrides, match):
    projects, handle = make_project(tmp_path)
    record = create_artifact(projects, handle.project_id, content=b"source")
    FactStore(projects).save(
        FactSet(
            project_id=handle.project_id,
            facts=(
                Fact(
                    key=PROJECT_MISSION_REQUEST_FACT_KEY,
                    value=mission_request(**overrides),
                    provenance=ProvenanceClass.USER_FACT,
                ),
            ),
        )
    )
    with pytest.raises(ProjectMissionMappingError, match=match):
        map_project_to_mission(projects, handle.project_id, [source_ref(record)])


def test_u112_does_not_perform_region_or_runtime_policy_validation(tmp_path: Path):
    projects, handle = make_project(tmp_path)
    record = create_artifact(projects, handle.project_id, content=b"source")
    FactStore(projects).save(
        FactSet(
            project_id=handle.project_id,
            facts=(
                Fact(
                    key=PROJECT_MISSION_REQUEST_FACT_KEY,
                    value=mission_request(waypoints=[{"north": 99999.0, "east": -99999.0, "altitude": 3.0}]),
                    provenance=ProvenanceClass.USER_FACT,
                ),
            ),
        )
    )
    proposal = map_project_to_mission(projects, handle.project_id, [source_ref(record)])
    assert proposal.to_dict()["mission_payload"]["steps"][1]["input"]["target_position_m"] == [99999.0, -99999.0, -3.0]
