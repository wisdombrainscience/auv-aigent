"""Frozen Phase-11 UAV Bridge contracts.

U11.1 defines the simulation proposal data contract. U11.2 adds deterministic
Project State -> MISSION-001 mapping. U11.3 adds deterministic bridge
validation. U11.4 adds an explicit deny-by-default capability allowlist. U11.5
adds an explicit deny-by-default mission allowlist. U11.6 invokes the existing
mission runtime only after authorization. U11.7 links finalized UAV runs back to Project State without changing verifier authority.
"""

from ai.uav_bridge.proposal import (
    ExecutionScope,
    MAX_SIMULATION_PROPOSAL_JSON_BYTES,
    MAX_SIMULATION_PROPOSAL_JSON_DEPTH,
    MAX_SIMULATION_PROPOSAL_JSON_NODES,
    MAX_SIMULATION_PROPOSAL_MISSION_ID_CHARS,
    MAX_SIMULATION_PROPOSAL_SCENARIO_BYTES,
    MAX_SIMULATION_PROPOSAL_SOURCE_ARTIFACTS,
    SIMULATION_PROPOSAL_CONTRACT_VERSION,
    SimulationProposal,
    SimulationProposalError,
    parse_simulation_proposal,
)

from ai.uav_bridge.validation import (
    BRIDGE_VALIDATION_CONTRACT_VERSION,
    MISSION_INPUT_POLICY_REL,
    validate_bridge_proposal,
)

from ai.uav_bridge.authorization import (
    BRIDGE_CAPABILITY_ALLOWLIST,
    BRIDGE_MISSION_ALLOWLIST,
    CAPABILITY_ALLOWLIST_CONTRACT_VERSION,
    MISSION_ALLOWLIST_CONTRACT_VERSION,
    authorize_bridge_capabilities,
    authorize_bridge_mission,
)

from ai.uav_bridge.runtime import (
    BridgeRuntimeInvocationDeniedError,
    RUNTIME_INVOCATION_CONTRACT_VERSION,
    invoke_existing_runtime,
)


from ai.uav_bridge.linkage import (
    MAX_UAV_RUN_EVIDENCE_JSON_BYTES,
    MAX_UAV_RUN_ID_CHARS,
    MAX_UAV_RUN_LINK_RECORD_JSON_BYTES,
    UAV_RUN_LINK_CONTRACT_VERSION,
    UAV_RUN_LINK_REGISTRY_DB_FILENAME,
    UAV_RUN_LINK_REGISTRY_SCHEMA_VERSION,
    UAVRunLinkConflictError,
    UAVRunLinkError,
    UAVRunLinkNotFoundError,
    UAVRunLinkRecord,
    UAVRunLinkStore,
    initialize_uav_run_link_registry,
    link_uav_run_to_project,
    verify_uav_run_link_registry,
)

from ai.uav_bridge.mapping import (
    MAPPED_MISSION_FRAME,
    MAPPED_MISSION_ID,
    MAX_PROJECT_MISSION_WAYPOINTS,
    PROJECT_MISSION_MAPPING_CONTRACT_VERSION,
    PROJECT_MISSION_REQUEST_FACT_KEY,
    ProjectMissionMappingError,
    map_project_to_mission,
)

__all__ = [
    "ExecutionScope",
    "MAX_SIMULATION_PROPOSAL_JSON_BYTES",
    "MAX_SIMULATION_PROPOSAL_JSON_DEPTH",
    "MAX_SIMULATION_PROPOSAL_JSON_NODES",
    "MAX_SIMULATION_PROPOSAL_MISSION_ID_CHARS",
    "MAX_SIMULATION_PROPOSAL_SCENARIO_BYTES",
    "MAX_SIMULATION_PROPOSAL_SOURCE_ARTIFACTS",
    "SIMULATION_PROPOSAL_CONTRACT_VERSION",
    "SimulationProposal",
    "SimulationProposalError",
    "parse_simulation_proposal",
    "MAPPED_MISSION_FRAME",
    "MAPPED_MISSION_ID",
    "MAX_PROJECT_MISSION_WAYPOINTS",
    "PROJECT_MISSION_MAPPING_CONTRACT_VERSION",
    "PROJECT_MISSION_REQUEST_FACT_KEY",
    "ProjectMissionMappingError",
    "map_project_to_mission",
    "BRIDGE_VALIDATION_CONTRACT_VERSION",
    "MISSION_INPUT_POLICY_REL",
    "validate_bridge_proposal",
    "BRIDGE_CAPABILITY_ALLOWLIST",
    "BRIDGE_MISSION_ALLOWLIST",
    "CAPABILITY_ALLOWLIST_CONTRACT_VERSION",
    "MISSION_ALLOWLIST_CONTRACT_VERSION",
    "authorize_bridge_capabilities",
    "authorize_bridge_mission",
    "BridgeRuntimeInvocationDeniedError",
    "RUNTIME_INVOCATION_CONTRACT_VERSION",
    "invoke_existing_runtime",
    "MAX_UAV_RUN_EVIDENCE_JSON_BYTES",
    "MAX_UAV_RUN_ID_CHARS",
    "MAX_UAV_RUN_LINK_RECORD_JSON_BYTES",
    "UAV_RUN_LINK_CONTRACT_VERSION",
    "UAV_RUN_LINK_REGISTRY_DB_FILENAME",
    "UAV_RUN_LINK_REGISTRY_SCHEMA_VERSION",
    "UAVRunLinkConflictError",
    "UAVRunLinkError",
    "UAVRunLinkNotFoundError",
    "UAVRunLinkRecord",
    "UAVRunLinkStore",
    "initialize_uav_run_link_registry",
    "link_uav_run_to_project",
    "verify_uav_run_link_registry",
]
