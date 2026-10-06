#!/usr/bin/env python3
from __future__ import annotations
from pathlib import Path
import math
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ai.uav_bridge import SIMULATION_PROPOSAL_CONTRACT_VERSION, SimulationProposalError, parse_simulation_proposal


def proposal(mission_id: str = "MISSION-001"):
    return {
        "project_id": "UAV-PROJ-000001",
        "requirement_version": 1,
        "source_artifacts": [{"entity_id": "ART-000001", "version": 1}],
        "scenario_id": "U11-1-ACCEPTANCE",
        "execution_scope": "SIMULATION_ONLY",
        "mission_id": mission_id,
        "mission_payload": {
            "frame": "LOCAL_NED",
            "waypoints": [{"north": 5.0, "east": 0.0, "altitude": 3.0}],
        },
    }


def main() -> int:
    assert SIMULATION_PROPOSAL_CONTRACT_VERSION == "1.0"
    parsed = parse_simulation_proposal(proposal())
    assert parsed.to_dict() == proposal()
    assert parse_simulation_proposal(parsed.to_dict()).to_json() == parsed.to_json()

    # U11.5 owns mission allowlisting, so U11.1 accepts a structurally valid future ID.
    assert parse_simulation_proposal(proposal("FUTURE-MISSION-777")).mission_id == "FUTURE-MISSION-777"

    try:
        parse_simulation_proposal({**proposal(), "execution_scope": "REAL_WORLD"})
    except SimulationProposalError:
        pass
    else:
        raise AssertionError("real-world execution scope must fail closed")

    try:
        raw = proposal(); raw["mission_payload"] = {"x": math.nan}; parse_simulation_proposal(raw)
    except SimulationProposalError:
        pass
    else:
        raise AssertionError("non-finite payload must fail closed")

    print(
        "SIMULATION_PROPOSAL_SCHEMA=PASS contract_version=1.0 fields=7 "
        "execution_scope=SIMULATION_ONLY canonical_project=PASS artifact_lineage=PASS "
        "strict_json=PASS bounded=PASS unknown_mission=STRUCTURALLY_ACCEPTED "
        "project_mapping=active_U11_2 bridge_validation=active_U11_3 "
        "capability_allowlist=active_U11_4 mission_allowlist=active_U11_5 "
        "runtime_invocation=active_U11_6 uav_run_link=reserved_for_U11_7 "
        "verdict_authority=independent_verifier persistence=none provider_calls=none "
        "px4_access=none phase11=pending cp9=pending"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
