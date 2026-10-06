from __future__ import annotations

import json
from pathlib import Path

import pytest

from ai.agents import (
    AGENT_RESULT_OUTPUT_SCHEMA_ID,
    AgentRunStore,
    ContextManifestStore,
    MinimalContextBuilder,
    ResultDraftArtifactStore,
    SchemaEnforcedOutputValidator,
)
from ai.artifacts import ArtifactVersionStore
from ai.artifacts.hashing import ArtifactContentStore
from ai.contracts.artifact_states import ArtifactState
from ai.contracts.validation_result import ValidationStatus
from ai.project.store import ProjectStore
from ai.providers.contracts import GenerateResponse
from ai.tasks import TaskCreateInput, TaskStore
from ai.validators import (
    MAX_MISSION_DISTANCE_POINTS,
    MAX_MISSION_DISTANCE_VALIDATION_BYTES,
    MISSION_DISTANCE_CALCULATION_KIND,
    MISSION_DISTANCE_DECIMAL_PRECISION,
    MISSION_DISTANCE_FRAME,
    MISSION_DISTANCE_QUANTUM_M,
    MISSION_DISTANCE_VALIDATOR_CONTRACT_VERSION,
    MISSION_DISTANCE_VALIDATOR_ID,
    DeterministicMissionDistanceValidator,
    MissionDistanceValidationConflictError,
)


CREATED_AT = "2026-10-05T19:00:00Z"


def task_input(*, validators=("schema", "finite_values", "units", "mission_distance")):
    return TaskCreateInput(
        title="Validate deterministic mission distance",
        role="uav_systems_engineer",
        priority=100,
        dependencies=(),
        input_artifacts=(),
        output_schema=AGENT_RESULT_OUTPUT_SCHEMA_ID,
        validators=tuple(validators),
        review_required=False,
        max_attempts=3,
    )


def mission_distance(
    *,
    frame="LOCAL_NED",
    points_m=None,
    reported_distance_m=26,
    extra=None,
):
    value = {
        "kind": "mission_distance",
        "frame": frame,
        "points_m": points_m
        if points_m is not None
        else [
            [0, 0, 0],
            [0, 0, -3],
            [3, 4, -3],
            [6, 8, -3],
            [0, 0, -3],
            [0, 0, 0],
        ],
        "reported_distance_m": reported_distance_m,
    }
    if extra:
        value.update(extra)
    return value


def response(*, calculations=None):
    payload = {
        "status": "PROPOSED",
        "claims": [
            {
                "classification": "CALCULATED_VALUE",
                "statement": "Deterministic mission-distance proposal.",
            }
        ],
        "assumptions": [],
        "calculations": calculations if calculations is not None else [mission_distance()],
        "artifacts": [],
        "risks": [],
        "missing_information": [],
        "open_questions": [],
    }
    return GenerateResponse(
        model="local-test",
        content=json.dumps(payload, separators=(",", ":"), allow_nan=False),
        done=True,
    )


def setup_valid(tmp_path: Path, *, calculations=None, validators=None):
    projects = ProjectStore(tmp_path / "remote-uav" / "projects")
    handle = projects.create_project()
    tasks = TaskStore(projects)
    task = tasks.create_task(
        handle.project_id,
        task_input(**({"validators": validators} if validators is not None else {})),
    )
    context = MinimalContextBuilder(projects).build(handle.project_id, task.task_id)
    output = SchemaEnforcedOutputValidator(projects).enforce(
        handle.project_id,
        task.task_id,
        response(calculations=calculations),
    )
    run = AgentRunStore(projects).create_run(output)
    ContextManifestStore(projects).create_manifest(run, context)
    artifact = ResultDraftArtifactStore(projects).create_artifact(
        handle.project_id, run.run_id, created_timestamp=CREATED_AT
    )
    return projects, handle, task, artifact


def validate(tmp_path: Path, calculation=None, *, calculations=None):
    if calculations is None:
        calculations = [calculation if calculation is not None else mission_distance()]
    projects, handle, task, artifact = setup_valid(tmp_path, calculations=calculations)
    result = DeterministicMissionDistanceValidator(projects).validate(
        handle.project_id, artifact.record.artifact_id, artifact.record.version
    )
    return projects, handle, task, artifact, result


def test_contract_constants_are_explicit():
    assert MISSION_DISTANCE_VALIDATOR_CONTRACT_VERSION == "1.0"
    assert MISSION_DISTANCE_VALIDATOR_ID == "mission_distance"
    assert MISSION_DISTANCE_CALCULATION_KIND == "mission_distance"
    assert MISSION_DISTANCE_FRAME == "LOCAL_NED"
    assert MAX_MISSION_DISTANCE_VALIDATION_BYTES == 10 * 1024 * 1024
    assert MAX_MISSION_DISTANCE_POINTS == 24
    assert MISSION_DISTANCE_DECIMAL_PRECISION == 50
    assert str(MISSION_DISTANCE_QUANTUM_M) == "0.000001"


def test_valid_3d_mission_distance_passes_with_evidence(tmp_path):
    _p, _h, _t, _a, result = validate(tmp_path)
    assert result.overall is ValidationStatus.PASS
    check = result.checks[0]
    assert check.check_id == "mission_distance"
    details = check.to_dict()["details"]
    assert details["frame"] == "LOCAL_NED"
    assert details["unit"] == "m"
    assert details["formula"] == "sum(sqrt(dn^2+de^2+dd^2))"
    assert details["point_count"] == 6
    assert details["segment_count"] == 5
    assert details["computed_distance_m"] == 26
    assert details["reported_distance_m"] == 26
    assert details["reconciled"] is True
    assert details["rounding"] == "ROUND_HALF_EVEN"


def test_vertical_motion_is_part_of_3d_distance(tmp_path):
    calc = mission_distance(points_m=[[0, 0, 0], [0, 0, -3]], reported_distance_m=3)
    _p, _h, _t, _a, result = validate(tmp_path, calc)
    assert result.overall is ValidationStatus.PASS
    assert result.checks[0].to_dict()["details"]["computed_distance_m"] == 3


def test_diagonal_distance_uses_fixed_quantization(tmp_path):
    calc = mission_distance(points_m=[[0, 0, 0], [1, 1, 0]], reported_distance_m=1.414214)
    _p, _h, _t, _a, result = validate(tmp_path, calc)
    assert result.overall is ValidationStatus.PASS
    assert result.checks[0].to_dict()["details"]["computed_distance_m"] == 1.414214


def test_reported_distance_mismatch_is_fail(tmp_path):
    _p, _h, _t, _a, result = validate(tmp_path, mission_distance(reported_distance_m=26.1))
    assert result.overall is ValidationStatus.FAIL
    assert "does not equal" in (result.checks[0].reason or "")
    assert result.checks[0].to_dict()["details"]["reconciled"] is False


def test_frame_must_be_local_ned(tmp_path):
    _p, _h, _t, _a, result = validate(tmp_path, mission_distance(frame="ENU"))
    assert result.overall is ValidationStatus.FAIL
    assert "exactly LOCAL_NED" in (result.checks[0].reason or "")


def test_points_m_must_be_array(tmp_path):
    _p, _h, _t, _a, result = validate(tmp_path, mission_distance(points_m="bad"))
    assert result.overall is ValidationStatus.FAIL
    assert "points_m must be an array" in (result.checks[0].reason or "")


def test_at_least_two_points_are_required(tmp_path):
    _p, _h, _t, _a, result = validate(
        tmp_path, mission_distance(points_m=[[0, 0, 0]], reported_distance_m=0)
    )
    assert result.overall is ValidationStatus.FAIL
    assert "at least two points" in (result.checks[0].reason or "")


def test_maximum_current_mission_builder_route_size_is_accepted(tmp_path):
    points = [[i, 0, 0] for i in range(MAX_MISSION_DISTANCE_POINTS)]
    _p, _h, _t, _a, result = validate(
        tmp_path,
        mission_distance(points_m=points, reported_distance_m=MAX_MISSION_DISTANCE_POINTS - 1),
    )
    assert result.overall is ValidationStatus.PASS


def test_more_than_maximum_points_fails(tmp_path):
    points = [[i, 0, 0] for i in range(MAX_MISSION_DISTANCE_POINTS + 1)]
    _p, _h, _t, _a, result = validate(
        tmp_path,
        mission_distance(points_m=points, reported_distance_m=MAX_MISSION_DISTANCE_POINTS),
    )
    assert result.overall is ValidationStatus.FAIL
    assert "MAX_MISSION_DISTANCE_POINTS" in (result.checks[0].reason or "")


def test_each_point_must_have_three_coordinates(tmp_path):
    _p, _h, _t, _a, result = validate(
        tmp_path,
        mission_distance(points_m=[[0, 0, 0], [1, 2]], reported_distance_m=0),
    )
    assert result.overall is ValidationStatus.FAIL
    assert "[north_m,east_m,down_m]" in (result.checks[0].reason or "")


@pytest.mark.parametrize("bad", [True, "1", None, [], {}])
def test_coordinate_must_be_numeric_not_bool(tmp_path, bad):
    _p, _h, _t, _a, result = validate(
        tmp_path,
        mission_distance(points_m=[[0, 0, 0], [bad, 0, 0]], reported_distance_m=1),
    )
    assert result.overall is ValidationStatus.FAIL
    assert "must be a number" in (result.checks[0].reason or "")


def test_negative_coordinates_are_valid_local_ned(tmp_path):
    _p, _h, _t, _a, result = validate(
        tmp_path,
        mission_distance(points_m=[[0, 0, 0], [-3, -4, 0]], reported_distance_m=5),
    )
    assert result.overall is ValidationStatus.PASS


def test_zero_length_segments_are_bookkeeping_valid(tmp_path):
    _p, _h, _t, _a, result = validate(
        tmp_path,
        mission_distance(points_m=[[0, 0, 0], [0, 0, 0], [3, 4, 0]], reported_distance_m=5),
    )
    assert result.overall is ValidationStatus.PASS


def test_reported_distance_must_be_nonnegative(tmp_path):
    _p, _h, _t, _a, result = validate(tmp_path, mission_distance(reported_distance_m=-1))
    assert result.overall is ValidationStatus.FAIL
    assert "must be non-negative" in (result.checks[0].reason or "")


@pytest.mark.parametrize("bad", [True, "26", None, [], {}])
def test_reported_distance_must_be_numeric_not_bool(tmp_path, bad):
    _p, _h, _t, _a, result = validate(tmp_path, mission_distance(reported_distance_m=bad))
    assert result.overall is ValidationStatus.FAIL
    assert "must be a number" in (result.checks[0].reason or "")


def test_calculation_keys_are_exact(tmp_path):
    _p, _h, _t, _a, result = validate(
        tmp_path, mission_distance(extra={"distance_tolerance_m": 0.5})
    )
    assert result.overall is ValidationStatus.FAIL
    assert "must contain exactly" in (result.checks[0].reason or "")


def test_missing_calculation_fails(tmp_path):
    _p, _h, _t, _a, result = validate(
        tmp_path, calculations=[{"kind": "mass_budget", "reported_total_kg": 3.5}]
    )
    assert result.overall is ValidationStatus.FAIL
    assert "must contain one mission_distance" in (result.checks[0].reason or "")


def test_multiple_calculations_fail(tmp_path):
    _p, _h, _t, _a, result = validate(
        tmp_path, calculations=[mission_distance(), mission_distance()]
    )
    assert result.overall is ValidationStatus.FAIL
    assert "exactly one mission_distance" in (result.checks[0].reason or "")


def test_unrelated_calculations_are_ignored(tmp_path):
    _p, _h, _t, _a, result = validate(
        tmp_path,
        calculations=[
            {"kind": "mass_budget", "reported_total_kg": 3.5},
            mission_distance(),
            {"kind": "reserve", "reserve_percent": 20},
        ],
    )
    assert result.overall is ValidationStatus.PASS


def test_task_must_request_validator(tmp_path):
    projects, handle, _task, artifact = setup_valid(
        tmp_path, validators=("schema", "finite_values", "units")
    )
    with pytest.raises(MissionDistanceValidationConflictError, match="did not request"):
        DeterministicMissionDistanceValidator(projects).validate(
            handle.project_id, artifact.record.artifact_id, 1
        )


def test_missing_artifact_is_conflict_not_engineering_fail(tmp_path):
    projects = ProjectStore(tmp_path / "projects")
    handle = projects.create_project()
    with pytest.raises(MissionDistanceValidationConflictError, match="opened safely"):
        DeterministicMissionDistanceValidator(projects).validate(
            handle.project_id, "ART-000001", 1
        )


def test_tampered_blob_is_conflict_not_engineering_fail(tmp_path):
    projects, handle, _task, artifact = setup_valid(tmp_path)
    blob = ArtifactContentStore(projects).blob_path(handle.project_id, artifact.record.hash)
    blob.write_bytes(blob.read_bytes() + b"tamper")
    with pytest.raises(MissionDistanceValidationConflictError, match="opened safely"):
        DeterministicMissionDistanceValidator(projects).validate(
            handle.project_id, artifact.record.artifact_id, 1
        )


def test_validation_does_not_change_draft_lifecycle(tmp_path):
    projects, handle, _task, artifact = setup_valid(tmp_path)
    DeterministicMissionDistanceValidator(projects).validate(
        handle.project_id, artifact.record.artifact_id, 1
    )
    reopened = ArtifactVersionStore(projects).open_version(
        handle.project_id, artifact.record.artifact_id, 1
    )
    assert reopened.status is ArtifactState.DRAFT
    assert reopened.validation_result is None


def test_validation_does_not_change_task_state(tmp_path):
    projects, handle, task, artifact = setup_valid(tmp_path)
    tasks = TaskStore(projects)
    before = tasks.open_task(handle.project_id, task.task_id)
    DeterministicMissionDistanceValidator(projects).validate(
        handle.project_id, artifact.record.artifact_id, 1
    )
    assert tasks.open_task(handle.project_id, task.task_id) == before


def test_restart_is_deterministic(tmp_path):
    projects, handle, _task, artifact = setup_valid(tmp_path)
    first = DeterministicMissionDistanceValidator(projects).validate(
        handle.project_id, artifact.record.artifact_id, 1
    )
    restarted = ProjectStore(projects.projects_root)
    second = DeterministicMissionDistanceValidator(restarted).validate(
        handle.project_id, artifact.record.artifact_id, 1
    )
    assert second == first
