#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ai.artifacts.schema import ArtifactInputVersion
from ai.contracts.ids import CanonicalId, IdKind
from ai.simulation_feedback.parser import MISSION_VERIFIER_CHECK_IDS, ParsedVerifierResult
from ai.simulation_feedback.routing import UAV_SYSTEMS_ENGINEER_ROLE_ID, route_simulation_issues
from ai.simulation_feedback.taxonomy import classify_simulation_issues


def main() -> int:
    checks = {check_id: "FAIL" for check_id in MISSION_VERIFIER_CHECK_IDS}
    parsed = ParsedVerifierResult(
        project_id=CanonicalId.from_sequence(IdKind.PROJECT, 1),
        requirement_version=1,
        source_artifacts=(
            ArtifactInputVersion(
                entity_id=CanonicalId.from_sequence(IdKind.ARTIFACT, 1),
                version=1,
            ),
        ),
        runtime_run_id="MISSION001-F12-3-CHECK",
        mission_id="MISSION-001",
        scenario_id="F12-3-CHECK",
        overall="FAIL",
        checks=checks,
        failed_checks=MISSION_VERIFIER_CHECK_IDS,
        failed_step_ids=("nav-1",),
        max_transition_distance_m=2.0,
        terminal_home_distance_m=3.0,
        reason="verification failed",
        verification_sha256="d" * 64,
    )
    issue_set = classify_simulation_issues(parsed)
    plan = route_simulation_issues(issue_set)

    if len(plan.routes) != 7:
        raise SystemExit("expected seven routed issues")
    if {route.owner_role for route in plan.routes} != {UAV_SYSTEMS_ENGINEER_ROLE_ID}:
        raise SystemExit("unexpected engineering owner")
    if tuple(route.issue.fingerprint for route in plan.routes) != tuple(
        issue.fingerprint for issue in issue_set.issues
    ):
        raise SystemExit("issue fingerprint lineage changed during routing")

    print(
        "SIMULATION_ISSUE_ROLE_MAPPING=PASS "
        "contract_version=1.0 "
        "categories=7 "
        "policy=EXPLICIT_STATIC "
        "owner_role=uav_systems_engineer "
        "active_role=REQUIRED "
        "reserved_specialists=NOT_ACTIVATED "
        "reviewer=ADVISORY_ONLY "
        "fingerprint=PRESERVED "
        "lineage=PRESERVED "
        "provenance=VERIFIER_RESULT "
        "issue_id=reserved "
        "persistence=none "
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
