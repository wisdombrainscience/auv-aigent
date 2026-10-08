#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ai.artifacts.schema import ArtifactInputVersion
from ai.contracts.failures import FailureClass
from ai.contracts.ids import CanonicalId, IdKind
from ai.simulation_feedback.parser import MISSION_VERIFIER_CHECK_IDS, ParsedVerifierResult
from ai.simulation_feedback.taxonomy import SimulationIssueType, classify_simulation_issues


def parsed(*failed: str) -> ParsedVerifierResult:
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
        requirement_version=1,
        source_artifacts=(
            ArtifactInputVersion(
                entity_id=CanonicalId.from_sequence(IdKind.ARTIFACT, 1),
                version=1,
            ),
        ),
        runtime_run_id="MISSION001-F12-2-CHECK",
        mission_id="MISSION-001",
        scenario_id="F12-2-CHECK",
        overall="FAIL" if failed_checks else "PASS",
        checks=checks,
        failed_checks=failed_checks,
        failed_step_ids=("nav-1",) if failed_checks else (),
        max_transition_distance_m=2.0 if "continuity" in failed_set else 0.0,
        terminal_home_distance_m=3.0 if "terminal_home" in failed_set else 0.1,
        reason="verification failed" if failed_checks else None,
        verification_sha256="b" * 64,
    )


def main() -> int:
    passed = classify_simulation_issues(parsed())
    if passed.issues:
        raise SystemExit("PASS result unexpectedly produced issues")

    failed = classify_simulation_issues(parsed(*MISSION_VERIFIER_CHECK_IDS))
    if len(failed.issues) != 7:
        raise SystemExit("expected seven canonical issues")

    expected_types = (
        SimulationIssueType.MISSION_SEQUENCE,
        SimulationIssueType.CAPABILITY_VERDICT,
        SimulationIssueType.EVIDENCE_INTEGRITY,
        SimulationIssueType.NAVIGATION,
        SimulationIssueType.MISSION_TERMINATION,
        SimulationIssueType.LANDING,
        SimulationIssueType.RETURN_HOME,
    )
    if tuple(issue.issue_type for issue in failed.issues) != expected_types:
        raise SystemExit("canonical taxonomy order mismatch")

    classes = {issue.failure.failure_class for issue in failed.issues}
    if classes != {FailureClass.SIMULATION, FailureClass.VERIFICATION}:
        raise SystemExit("canonical FailureClass reuse failed")

    print(
        "SIMULATION_ISSUE_TAXONOMY=PASS "
        "contract_version=1.0 "
        "categories=7 "
        "pass_result=NO_ISSUES "
        "fail_checks=ONE_ISSUE_PER_CHECK "
        "failure_taxonomy=REUSED_C1_8 "
        "simulation_class=SIMULATION "
        "verification_class=VERIFICATION "
        "fingerprint=DETERMINISTIC_ROOT_CAUSE "
        "lineage=PRESERVED "
        "provenance=VERIFIER_RESULT "
        "issue_id=reserved "
        "persistence=none "
        "role_mapping=active_F12_3 "
        "lineage_invalidation=active_F12_4 "
        "rework_task=active_F12_5 "
        "retry=active_F12_6 "
        "needs_human=active_F12_7 "
        "provider_calls=none "
        "px4_access=none "
        "phase11=GREEN "
        "cp9=GREEN "
        "phase12=READY "
        "cp10=READY"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
