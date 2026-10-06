from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from ai.contracts.validation_result import ValidationStatus
from ai.uav_bridge.authorization import (
    MISSION_ALLOWLIST_CONTRACT_VERSION,
    authorize_bridge_mission,
)
from ai.uav_bridge.proposal import SimulationProposal
from orchestration.mission_experiment import (
    MissionExperimentResult,
    MissionExperimentRunner,
)


RUNTIME_INVOCATION_CONTRACT_VERSION = "1.0"


class BridgeRuntimeInvocationDeniedError(PermissionError):
    """Raised when a proposal is not authorized to cross into the UAV runtime."""


def _failed_check_ids(result: Any) -> tuple[str, ...]:
    return tuple(
        check.check_id
        for check in result.checks
        if check.status is ValidationStatus.FAIL
    )


async def invoke_existing_runtime(
    project_root: Path,
    proposal: SimulationProposal,
    *,
    adapter: Any,
    step_hook: Callable[[dict[str, Any]], None] | None = None,
    step_event_hook: Callable[[dict[str, Any]], None] | None = None,
) -> MissionExperimentResult:
    """Invoke the existing mission runtime after U11.5 authorization.

    U11.6 is deliberately a narrow adapter boundary:

    * U11.5 authorization must PASS before any runtime object is constructed.
    * The proposal mission payload and scenario ID are forwarded unchanged.
    * Execution is delegated to the existing ``MissionExperimentRunner``.
    * The returned ``MissionExperimentResult`` is returned unchanged; U11.6
      never promotes, rewrites, or substitutes the independent verifier verdict.
    * The bridge does not create Project-State/UAV-run linkage (reserved for
      U11.7), does not instantiate a PX4 adapter, and does not call MAVSDK.

    ``adapter`` is a trusted runtime dependency supplied by the existing runtime
    integration layer. It is opaque to the bridge and is forwarded unchanged.
    """

    if not isinstance(proposal, SimulationProposal):
        raise TypeError("proposal must be SimulationProposal")
    if adapter is None:
        raise TypeError("adapter must be a trusted runtime adapter instance")

    root = Path(project_root).resolve()

    authorization = authorize_bridge_mission(root, proposal)
    if authorization.overall is not ValidationStatus.PASS:
        failed = _failed_check_ids(authorization)
        detail = ",".join(failed) if failed else "unknown"
        raise BridgeRuntimeInvocationDeniedError(
            "U11.5 mission authorization must pass before runtime invocation "
            f"(contract={MISSION_ALLOWLIST_CONTRACT_VERSION}; failed={detail})"
        )

    serialized = proposal.to_dict()
    mission_payload = serialized["mission_payload"]
    if not isinstance(mission_payload, dict):
        # SimulationProposal already guarantees this invariant. Keep a defensive
        # fail-closed guard at the runtime boundary.
        raise BridgeRuntimeInvocationDeniedError(
            "authorized proposal mission_payload is not a runtime JSON object"
        )

    runner = MissionExperimentRunner(root)
    return await runner.run(
        mission_data=mission_payload,
        scenario_id=proposal.scenario_id,
        adapter=adapter,
        step_hook=step_hook,
        step_event_hook=step_event_hook,
    )
