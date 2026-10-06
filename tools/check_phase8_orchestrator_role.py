#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
import sys

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from ai.agents import DEFAULT_ROLE_REGISTRY
from ai.orchestrator import (
    DEFAULT_ORCHESTRATOR_ROLE,
    ProposedTask,
    TaskPlanProposal,
    create_orchestrator_agent,
)


def main() -> int:
    roles_before = DEFAULT_ROLE_REGISTRY.role_ids()
    proposal = TaskPlanProposal(
        project_id="UAV-PROJ-000001",
        requirement_version=1,
        tasks=(
            ProposedTask(
                task_id="TASK-000001",
                title="Deferred plan validation",
                role="unknown_but_well_formed_role",
                priority=10,
                dependencies=("TASK-000999",),
                input_artifacts=(),
                output_schema="unknown.schema.v1",
                validators=("schema",),
                review_required=True,
                max_attempts=4,
            ),
        ),
    )
    accepted = DEFAULT_ORCHESTRATOR_ROLE.require_proposal(proposal)
    agent = create_orchestrator_agent(instance_key="ORCH:UAV-PROJ-000001:v1")
    roles_after = DEFAULT_ROLE_REGISTRY.role_ids()

    assert accepted is proposal
    assert DEFAULT_ORCHESTRATOR_ROLE.role_id == "coordinator"
    assert agent.role_id == "coordinator"
    assert roles_before == roles_after
    assert roles_after == (
        "coordinator",
        "requirements_engineer",
        "reviewer",
        "uav_systems_engineer",
    )
    assert DEFAULT_ROLE_REGISTRY.contains("orchestrator") is False

    print(
        "ORCHESTRATOR_ROLE=PASS "
        "role=coordinator registry_roles=4 new_role_id=none "
        "proposal_contract=TaskPlanProposal logical_agent=PASS "
        "semantic_validation=deferred plan_validator=reserved_for_O8_3 "
        "known_role=reserved_for_O8_4 known_schema=reserved_for_O8_5 "
        "dependency_validation=reserved_for_O8_6 resource_budget=reserved_for_O8_7 "
        "approved_dag=reserved_for_O8_8 persistence=none task_execution=none "
        "provider_calls=none cp8=pending"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
