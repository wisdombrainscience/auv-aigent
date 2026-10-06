"""Phase-8 minimal orchestrator contracts.

O8.1 defines the non-executable TaskPlanProposal contract. O8.2 binds the
orchestration capability to the frozen MVP ``coordinator`` role without adding
a new role ID or granting plan-validation/execution authority. O8.3 adds the
deterministic PlanValidator boundary and unique-task-ID check. O8.4 adds
RoleRegistry-backed known-role validation without changing the frozen role set.
O8.5 adds OutputSchemaRegistry-backed known-output-schema validation. O8.6 adds
dependency existence, self-dependency, and DAG acyclicity validation. O8.7 adds
frozen planner/runtime resource-budget validation. O8.8 materializes a fully
approved proposal into the existing persistent Task DAG without scheduling it.
"""

from ai.orchestrator.proposal import (
    MAX_PROPOSED_MAX_ATTEMPTS,
    MAX_TASK_PLAN_PROPOSAL_BYTES,
    MAX_TASK_PLAN_PROPOSAL_TASKS,
    TASK_PLAN_PROPOSAL_CONTRACT_VERSION,
    ProposedTask,
    TaskPlanProposal,
    TaskPlanProposalError,
    parse_task_plan_proposal,
    parse_task_plan_proposal_json,
    serialize_task_plan_proposal,
)

from ai.orchestrator.validator import (
    DEFAULT_PLAN_VALIDATOR,
    MAX_DEPENDENCY_DEPTH,
    MAX_TASKS_PER_PLAN,
    PLAN_CHECK_DAG_ACYCLIC,
    PLAN_CHECK_DEPENDENCY_DEPTH_BUDGET,
    PLAN_CHECK_DEPENDENCY_EXISTENCE,
    PLAN_CHECK_KNOWN_OUTPUT_SCHEMAS,
    PLAN_CHECK_KNOWN_ROLES,
    PLAN_CHECK_NO_SELF_DEPENDENCIES,
    PLAN_CHECK_RETRY_BUDGET,
    PLAN_CHECK_RUNTIME_RESOURCE_BUDGET,
    PLAN_CHECK_TASK_COUNT_BUDGET,
    PLAN_CHECK_UNIQUE_TASK_IDS,
    PLAN_VALIDATOR_CHECK_IDS,
    PLAN_VALIDATOR_CONTRACT_VERSION,
    PlanValidator,
    PlanValidatorError,
    validate_task_plan_proposal,
)
from ai.orchestrator.role import (
    DEFAULT_ORCHESTRATOR_ROLE,
    ORCHESTRATOR_OUTPUT_CONTRACT,
    ORCHESTRATOR_ROLE_CONTRACT_VERSION,
    ORCHESTRATOR_ROLE_ID,
    ORCHESTRATOR_ROLE_TITLE,
    OrchestratorRoleError,
    OrchestratorRoleProfile,
    bind_orchestrator_role,
    create_orchestrator_agent,
)

from ai.orchestrator.persistence import (
    APPROVED_DAG_DIRECTORY_NAME,
    APPROVED_DAG_JOURNAL_FILENAME,
    APPROVED_DAG_PERSISTENCE_CONTRACT_VERSION,
    APPROVED_DAG_RECEIPT_FORMAT_VERSION,
    ApprovedDAGConflictError,
    ApprovedDAGPersistenceError,
    ApprovedDAGReceipt,
    ApprovedDAGStore,
    ApprovedDAGValidationError,
    TaskIdBinding,
    persist_approved_task_dag,
)

__all__ = [
    "APPROVED_DAG_DIRECTORY_NAME",
    "APPROVED_DAG_JOURNAL_FILENAME",
    "APPROVED_DAG_PERSISTENCE_CONTRACT_VERSION",
    "APPROVED_DAG_RECEIPT_FORMAT_VERSION",
    "ApprovedDAGConflictError",
    "ApprovedDAGPersistenceError",
    "ApprovedDAGReceipt",
    "ApprovedDAGStore",
    "ApprovedDAGValidationError",
    "TaskIdBinding",
    "persist_approved_task_dag",
    "MAX_PROPOSED_MAX_ATTEMPTS",
    "MAX_TASK_PLAN_PROPOSAL_BYTES",
    "MAX_TASK_PLAN_PROPOSAL_TASKS",
    "TASK_PLAN_PROPOSAL_CONTRACT_VERSION",
    "ProposedTask",
    "TaskPlanProposal",
    "TaskPlanProposalError",
    "parse_task_plan_proposal",
    "parse_task_plan_proposal_json",
    "serialize_task_plan_proposal",
    "DEFAULT_ORCHESTRATOR_ROLE",
    "ORCHESTRATOR_OUTPUT_CONTRACT",
    "ORCHESTRATOR_ROLE_CONTRACT_VERSION",
    "ORCHESTRATOR_ROLE_ID",
    "ORCHESTRATOR_ROLE_TITLE",
    "OrchestratorRoleError",
    "OrchestratorRoleProfile",
    "bind_orchestrator_role",
    "create_orchestrator_agent",
    "DEFAULT_PLAN_VALIDATOR",
    "MAX_DEPENDENCY_DEPTH",
    "MAX_TASKS_PER_PLAN",
    "PLAN_CHECK_DAG_ACYCLIC",
    "PLAN_CHECK_DEPENDENCY_DEPTH_BUDGET",
    "PLAN_CHECK_DEPENDENCY_EXISTENCE",
    "PLAN_CHECK_KNOWN_OUTPUT_SCHEMAS",
    "PLAN_CHECK_KNOWN_ROLES",
    "PLAN_CHECK_NO_SELF_DEPENDENCIES",
    "PLAN_CHECK_RETRY_BUDGET",
    "PLAN_CHECK_RUNTIME_RESOURCE_BUDGET",
    "PLAN_CHECK_TASK_COUNT_BUDGET",
    "PLAN_CHECK_UNIQUE_TASK_IDS",
    "PLAN_VALIDATOR_CHECK_IDS",
    "PLAN_VALIDATOR_CONTRACT_VERSION",
    "PlanValidator",
    "PlanValidatorError",
    "validate_task_plan_proposal",
]
