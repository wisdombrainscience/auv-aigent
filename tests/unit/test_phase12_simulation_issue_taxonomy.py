from __future__ import annotations

import pytest

from ai.artifacts.schema import ArtifactInputVersion
from ai.contracts.failures import FailureClass
from ai.contracts.ids import CanonicalId, IdKind
from ai.contracts.provenance import ProvenanceClass
from ai.simulation_feedback.parser import (
    MISSION_VERIFIER_CHECK_IDS,
    ParsedVerifierResult,
)
from ai.simulation_feedback.taxonomy import (
    MAX_SIMULATION_ISSUES,
    SIMULATION_ISSUE_FINGERPRINT_ALGORITHM,
    SIMULATION_ISSUE_TAXONOMY_CONTRACT_VERSION,
    SimulationIssueTaxonomyError,
    SimulationIssueType,
    classify_simulation_issues,
)


EXPECTED = {
    "step_sequence": (
        SimulationIssueType.MISSION_SEQUENCE,
        FailureClass.VERIFICATION,
        "VERIFICATION_MISSION_SEQUENCE",
    ),
    "child_verdicts": (
        SimulationIssueType.CAPABILITY_VERDICT,
        FailureClass.VERIFICATION,
        "VERIFICATION_CHILD_VERDICT",
    ),
    "child_evidence_integrity": (
        SimulationIssueType.EVIDENCE_INTEGRITY,
        FailureClass.VERIFICATION,
        "VERIFICATION_CHILD_EVIDENCE_INTEGRITY",
    ),
    "continuity": (
        SimulationIssueType.NAVIGATION,
        FailureClass.SIMULATION,
        "SIMULATION_NAVIGATION_CONTINUITY",
    ),
    "terminal_land_step": (
        SimulationIssueType.MISSION_TERMINATION,
        FailureClass.VERIFICATION,
        "VERIFICATION_TERMINAL_LAND_STEP",
    ),
    "terminal_landed_state": (
        SimulationIssueType.LANDING,
        FailureClass.SIMULATION,
        "SIMULATION_TERMINAL_LANDED_STATE",
    ),
    "terminal_home": (
        SimulationIssueType.RETURN_HOME,
        FailureClass.SIMULATION,
        "SIMULATION_TERMINAL_HOME_POSITION",
    ),
}


def _parsed(*failed: str, runtime_run_id: str = "MISSION001-F12-2-RUN") -> ParsedVerifierResult:
    failed_set = set(failed)
    checks = {
        check_id: ("FAIL" if check_id in failed_set else "PASS")
        for check_id in MISSION_VERIFIER_CHECK_IDS
    }
    failed_checks = tuple(
        check_id for check_id in MISSION_VERIFIER_CHECK_IDS if check_id in failed_set
    )
    return ParsedVerifierResult(
        project_id=CanonicalId.from_sequence(IdKind.PROJECT, 1),
        requirement_version=3,
        source_artifacts=(
            ArtifactInputVersion(
                entity_id=CanonicalId.from_sequence(IdKind.ARTIFACT, 7),
                version=2,
            ),
        ),
        runtime_run_id=runtime_run_id,
        mission_id="MISSION-001",
        scenario_id="F12-2-TAXONOMY",
        overall="FAIL" if failed_checks else "PASS",
        checks=checks,
        failed_checks=failed_checks,
        failed_step_ids=("nav-1", "land") if failed_checks else (),
        max_transition_distance_m=2.75 if "continuity" in failed_set else 0.0,
        terminal_home_distance_m=4.5 if "terminal_home" in failed_set else 0.1,
        reason="mission verification failed" if failed_checks else None,
        verification_sha256="a" * 64,
    )


def test_contract_constants_are_frozen():
    assert SIMULATION_ISSUE_TAXONOMY_CONTRACT_VERSION == "1.0"
    assert SIMULATION_ISSUE_FINGERPRINT_ALGORITHM == "sha256"
    assert MAX_SIMULATION_ISSUES == 7


def test_pass_result_produces_no_issues_and_preserves_lineage():
    parsed = _parsed()
    result = classify_simulation_issues(parsed)

    assert result.issues == ()
    assert result.project_id == parsed.project_id
    assert result.requirement_version == parsed.requirement_version
    assert result.source_artifacts == parsed.source_artifacts
    assert result.runtime_run_id == parsed.runtime_run_id
    assert result.verification_sha256 == parsed.verification_sha256
    assert result.provenance is ProvenanceClass.VERIFIER_RESULT
    raw = result.to_dict()
    assert raw["contract_version"] == "1.0"
    assert raw["issues"] == []


@pytest.mark.parametrize("check_id", MISSION_VERIFIER_CHECK_IDS)
def test_each_verifier_check_has_one_deterministic_taxonomy_mapping(check_id: str):
    result = classify_simulation_issues(_parsed(check_id))
    assert len(result.issues) == 1
    issue = result.issues[0]
    expected_type, expected_class, expected_code = EXPECTED[check_id]
    assert issue.check_id == check_id
    assert issue.issue_type is expected_type
    assert issue.failure.failure_class is expected_class
    assert issue.failure.code == expected_code
    assert issue.fingerprint.startswith("sha256:")
    assert len(issue.fingerprint) == len("sha256:") + 64
    assert issue.provenance is ProvenanceClass.VERIFIER_RESULT


def test_multi_failure_preserves_canonical_verifier_order():
    result = classify_simulation_issues(
        _parsed("terminal_home", "child_evidence_integrity", "continuity")
    )
    assert tuple(issue.check_id for issue in result.issues) == (
        "child_evidence_integrity",
        "continuity",
        "terminal_home",
    )


def test_fingerprint_is_root_cause_stable_across_runtime_runs():
    first = classify_simulation_issues(
        _parsed("continuity", runtime_run_id="MISSION001-A")
    ).issues[0]
    second = classify_simulation_issues(
        _parsed("continuity", runtime_run_id="MISSION001-B")
    ).issues[0]
    assert first.fingerprint == second.fingerprint
    assert first.failure.to_dict()["details"]["runtime_run_id"] != second.failure.to_dict()["details"]["runtime_run_id"]


def test_details_preserve_observation_without_changing_root_identity():
    nav = classify_simulation_issues(_parsed("continuity")).issues[0]
    details = nav.failure.to_dict()["details"]
    assert details["max_transition_distance_m"] == 2.75
    assert details["failed_step_ids"] == ["nav-1", "land"]
    assert details["verifier_reason"] == "mission verification failed"

    home = classify_simulation_issues(_parsed("terminal_home")).issues[0]
    assert home.failure.to_dict()["details"]["terminal_home_distance_m"] == 4.5


def test_classifier_reuses_canonical_failure_taxonomy_not_parallel_class_strings():
    issues = classify_simulation_issues(_parsed(*MISSION_VERIFIER_CHECK_IDS)).issues
    assert {issue.failure.failure_class for issue in issues} == {
        FailureClass.SIMULATION,
        FailureClass.VERIFICATION,
    }
    assert all(issue.failure.code.startswith(issue.failure.failure_class.value + "_") for issue in issues)


def test_serialized_issue_set_has_no_owner_task_retry_or_issue_id_fields():
    raw = classify_simulation_issues(_parsed("terminal_home")).to_dict()
    encoded = repr(raw)
    assert "owner_role" not in encoded
    assert "rework_task" not in encoded
    assert "retry_attempt" not in encoded
    assert "issue_id" not in encoded


def test_wrong_input_type_fails_closed():
    with pytest.raises(TypeError, match="ParsedVerifierResult"):
        classify_simulation_issues({})  # type: ignore[arg-type]


def test_issue_set_rejects_duplicate_issue_fingerprints():
    result = classify_simulation_issues(_parsed("continuity"))
    issue = result.issues[0]
    with pytest.raises(SimulationIssueTaxonomyError, match="duplicate fingerprint"):
        type(result)(
            project_id=result.project_id,
            requirement_version=result.requirement_version,
            source_artifacts=result.source_artifacts,
            runtime_run_id=result.runtime_run_id,
            mission_id=result.mission_id,
            scenario_id=result.scenario_id,
            verification_sha256=result.verification_sha256,
            issues=(issue, issue),
        )
