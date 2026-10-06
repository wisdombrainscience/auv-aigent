from __future__ import annotations

from dataclasses import dataclass
import heapq
from typing import Final

from ai.agents.output import (
    DEFAULT_OUTPUT_SCHEMA_REGISTRY,
    OutputSchemaRegistry,
)
from ai.agents.roles import DEFAULT_ROLE_REGISTRY, RoleRegistry
from ai.contracts.validation_result import (
    ValidationCheck,
    ValidationResult,
    ValidationStatus,
)
from ai.orchestrator.proposal import TaskPlanProposal
from ai.model_manager import MAX_LOADED_MODELS
from ai.tasks.concurrency import MAX_PARALLEL_AGENT_TASKS
from ai.tasks.dag import _find_cycle
from ai.tasks.lifecycle import TASK_TIMEOUT_S
from ai.tasks.schema import MAX_TASK_ATTEMPTS


PLAN_VALIDATOR_CONTRACT_VERSION = "1.0"
PLAN_CHECK_UNIQUE_TASK_IDS = "plan.unique_task_ids"
PLAN_CHECK_KNOWN_ROLES = "plan.known_roles"
PLAN_CHECK_KNOWN_OUTPUT_SCHEMAS = "plan.known_output_schemas"
PLAN_CHECK_DEPENDENCY_EXISTENCE = "plan.dependency_existence"
PLAN_CHECK_NO_SELF_DEPENDENCIES = "plan.no_self_dependencies"
PLAN_CHECK_DAG_ACYCLIC = "plan.dag_acyclic"
PLAN_CHECK_TASK_COUNT_BUDGET = "plan.task_count_budget"
PLAN_CHECK_DEPENDENCY_DEPTH_BUDGET = "plan.dependency_depth_budget"
PLAN_CHECK_RETRY_BUDGET = "plan.retry_budget"
PLAN_CHECK_RUNTIME_RESOURCE_BUDGET = "plan.runtime_resource_budget"

# Frozen V8 planner/resource policy.  These approval limits are intentionally
# tighter than the O8.1 defensive wire bounds.
MAX_TASKS_PER_PLAN = 30
MAX_DEPENDENCY_DEPTH = 12
FROZEN_MAX_PARALLEL_AGENTS = 1
FROZEN_MAX_LOADED_MODELS = 1
FROZEN_TASK_TIMEOUT_S = 300.0
PLAN_VALIDATOR_CHECK_IDS: Final[tuple[str, ...]] = (
    PLAN_CHECK_UNIQUE_TASK_IDS,
    PLAN_CHECK_KNOWN_ROLES,
    PLAN_CHECK_KNOWN_OUTPUT_SCHEMAS,
    PLAN_CHECK_DEPENDENCY_EXISTENCE,
    PLAN_CHECK_NO_SELF_DEPENDENCIES,
    PLAN_CHECK_DAG_ACYCLIC,
    PLAN_CHECK_TASK_COUNT_BUDGET,
    PLAN_CHECK_DEPENDENCY_DEPTH_BUDGET,
    PLAN_CHECK_RETRY_BUDGET,
    PLAN_CHECK_RUNTIME_RESOURCE_BUDGET,
)


class PlanValidatorError(ValueError):
    """Raised when the Phase-8 PlanValidator is invoked with invalid input."""


def _unique_task_ids_check(proposal: TaskPlanProposal) -> ValidationCheck:
    task_ids = tuple(task.task_id.value for task in proposal.tasks)
    seen: set[str] = set()
    duplicate_ids: list[str] = []
    duplicate_seen: set[str] = set()

    for task_id in task_ids:
        if task_id in seen and task_id not in duplicate_seen:
            duplicate_ids.append(task_id)
            duplicate_seen.add(task_id)
        seen.add(task_id)

    if duplicate_ids:
        return ValidationCheck.from_dict(
            {
                "check_id": PLAN_CHECK_UNIQUE_TASK_IDS,
                "status": ValidationStatus.FAIL.value,
                "field": "tasks[].task_id",
                "reason": "task plan proposal contains duplicate task IDs",
                "details": {
                    "task_count": len(task_ids),
                    "unique_task_count": len(seen),
                    "duplicate_task_ids": duplicate_ids,
                },
            }
        )

    return ValidationCheck.from_dict(
        {
            "check_id": PLAN_CHECK_UNIQUE_TASK_IDS,
            "status": ValidationStatus.PASS.value,
            "field": "tasks[].task_id",
            "reason": None,
            "details": {
                "task_count": len(task_ids),
                "unique_task_count": len(seen),
                "duplicate_task_ids": [],
            },
        }
    )


def _known_roles_check(
    proposal: TaskPlanProposal,
    role_registry: RoleRegistry,
) -> ValidationCheck:
    known_role_ids = role_registry.role_ids()
    unknown_roles: list[str] = []
    unknown_seen: set[str] = set()
    unknown_assignments: list[dict[str, str]] = []

    for task in proposal.tasks:
        role_id = task.role
        if role_registry.contains(role_id):
            continue
        unknown_assignments.append(
            {
                "task_id": task.task_id.value,
                "role": role_id,
            }
        )
        if role_id not in unknown_seen:
            unknown_roles.append(role_id)
            unknown_seen.add(role_id)

    details = {
        "task_count": len(proposal.tasks),
        "registered_role_count": len(known_role_ids),
        "registered_role_ids": list(known_role_ids),
        "unknown_roles": unknown_roles,
        "unknown_assignments": unknown_assignments,
    }

    if unknown_assignments:
        return ValidationCheck.from_dict(
            {
                "check_id": PLAN_CHECK_KNOWN_ROLES,
                "status": ValidationStatus.FAIL.value,
                "field": "tasks[].role",
                "reason": "task plan proposal contains roles not registered in RoleRegistry",
                "details": details,
            }
        )

    return ValidationCheck.from_dict(
        {
            "check_id": PLAN_CHECK_KNOWN_ROLES,
            "status": ValidationStatus.PASS.value,
            "field": "tasks[].role",
            "reason": None,
            "details": details,
        }
    )


def _known_output_schemas_check(
    proposal: TaskPlanProposal,
    schema_registry: OutputSchemaRegistry,
) -> ValidationCheck:
    registered_schema_ids = schema_registry.schema_ids()
    unknown_schemas: list[str] = []
    unknown_seen: set[str] = set()
    unknown_assignments: list[dict[str, str]] = []

    for task in proposal.tasks:
        schema_id = task.output_schema
        if schema_registry.contains(schema_id):
            continue
        unknown_assignments.append(
            {
                "task_id": task.task_id.value,
                "output_schema": schema_id,
            }
        )
        if schema_id not in unknown_seen:
            unknown_schemas.append(schema_id)
            unknown_seen.add(schema_id)

    details = {
        "task_count": len(proposal.tasks),
        "registered_schema_count": len(registered_schema_ids),
        "registered_schema_ids": list(registered_schema_ids),
        "unknown_schemas": unknown_schemas,
        "unknown_assignments": unknown_assignments,
    }

    if unknown_assignments:
        return ValidationCheck.from_dict(
            {
                "check_id": PLAN_CHECK_KNOWN_OUTPUT_SCHEMAS,
                "status": ValidationStatus.FAIL.value,
                "field": "tasks[].output_schema",
                "reason": (
                    "task plan proposal contains output schemas not registered "
                    "in OutputSchemaRegistry"
                ),
                "details": details,
            }
        )

    return ValidationCheck.from_dict(
        {
            "check_id": PLAN_CHECK_KNOWN_OUTPUT_SCHEMAS,
            "status": ValidationStatus.PASS.value,
            "field": "tasks[].output_schema",
            "reason": None,
            "details": details,
        }
    )


def _dependency_existence_check(proposal: TaskPlanProposal) -> ValidationCheck:
    known_ids = {task.task_id.value for task in proposal.tasks}
    missing: list[dict[str, str]] = []
    missing_ids: list[str] = []
    missing_seen: set[str] = set()
    edge_count = 0

    for task in proposal.tasks:
        for dependency in task.dependencies:
            edge_count += 1
            dependency_id = dependency.value
            if dependency_id in known_ids:
                continue
            missing.append({
                "task_id": task.task_id.value,
                "dependency_task_id": dependency_id,
            })
            if dependency_id not in missing_seen:
                missing_ids.append(dependency_id)
                missing_seen.add(dependency_id)

    details = {
        "task_count": len(proposal.tasks),
        "edge_count": edge_count,
        "missing_dependency_ids": missing_ids,
        "missing_dependencies": missing,
    }
    if missing:
        return ValidationCheck.from_dict({
            "check_id": PLAN_CHECK_DEPENDENCY_EXISTENCE,
            "status": ValidationStatus.FAIL.value,
            "field": "tasks[].dependencies",
            "reason": "task plan proposal contains dependencies that do not exist in the proposal",
            "details": details,
        })
    return ValidationCheck.from_dict({
        "check_id": PLAN_CHECK_DEPENDENCY_EXISTENCE,
        "status": ValidationStatus.PASS.value,
        "field": "tasks[].dependencies",
        "reason": None,
        "details": details,
    })


def _no_self_dependencies_check(proposal: TaskPlanProposal) -> ValidationCheck:
    self_dependencies: list[dict[str, str]] = []
    for task in proposal.tasks:
        task_id = task.task_id.value
        for dependency in task.dependencies:
            if dependency.value == task_id:
                self_dependencies.append({
                    "task_id": task_id,
                    "dependency_task_id": dependency.value,
                })

    details = {
        "task_count": len(proposal.tasks),
        "self_dependencies": self_dependencies,
    }
    if self_dependencies:
        return ValidationCheck.from_dict({
            "check_id": PLAN_CHECK_NO_SELF_DEPENDENCIES,
            "status": ValidationStatus.FAIL.value,
            "field": "tasks[].dependencies",
            "reason": "task plan proposal contains self-dependencies",
            "details": details,
        })
    return ValidationCheck.from_dict({
        "check_id": PLAN_CHECK_NO_SELF_DEPENDENCIES,
        "status": ValidationStatus.PASS.value,
        "field": "tasks[].dependencies",
        "reason": None,
        "details": details,
    })


def _dag_acyclic_check(proposal: TaskPlanProposal) -> ValidationCheck:
    task_ids = tuple(task.task_id.value for task in proposal.tasks)
    duplicate_ids: list[str] = []
    seen: set[str] = set()
    duplicate_seen: set[str] = set()
    for task_id in task_ids:
        if task_id in seen and task_id not in duplicate_seen:
            duplicate_ids.append(task_id)
            duplicate_seen.add(task_id)
        seen.add(task_id)

    known_ids = set(task_ids)
    missing: list[dict[str, str]] = []
    for task in proposal.tasks:
        for dependency in task.dependencies:
            if dependency.value not in known_ids:
                missing.append({
                    "task_id": task.task_id.value,
                    "dependency_task_id": dependency.value,
                })

    if duplicate_ids or missing:
        details = {
            "task_count": len(proposal.tasks),
            "edge_count": sum(len(task.dependencies) for task in proposal.tasks),
            "cycle": [],
            "blocked_by_duplicate_task_ids": duplicate_ids,
            "blocked_by_missing_dependencies": missing,
        }
        return ValidationCheck.from_dict({
            "check_id": PLAN_CHECK_DAG_ACYCLIC,
            "status": ValidationStatus.FAIL.value,
            "field": "tasks[].dependencies",
            "reason": "task plan DAG acyclicity cannot be established until graph identity/existence errors are fixed",
            "details": details,
        })

    dependencies = {
        task.task_id.value: tuple(dep.value for dep in task.dependencies)
        for task in proposal.tasks
    }
    cycle = _find_cycle(dependencies)
    cycle_ids = [] if cycle is None else [item.value for item in cycle.task_ids]
    details = {
        "task_count": len(proposal.tasks),
        "edge_count": sum(len(task.dependencies) for task in proposal.tasks),
        "cycle": cycle_ids,
        "blocked_by_duplicate_task_ids": [],
        "blocked_by_missing_dependencies": [],
    }
    if cycle is not None:
        return ValidationCheck.from_dict({
            "check_id": PLAN_CHECK_DAG_ACYCLIC,
            "status": ValidationStatus.FAIL.value,
            "field": "tasks[].dependencies",
            "reason": "task plan proposal dependency graph contains a cycle",
            "details": details,
        })
    return ValidationCheck.from_dict({
        "check_id": PLAN_CHECK_DAG_ACYCLIC,
        "status": ValidationStatus.PASS.value,
        "field": "tasks[].dependencies",
        "reason": None,
        "details": details,
    })


def _task_count_budget_check(proposal: TaskPlanProposal) -> ValidationCheck:
    task_count = len(proposal.tasks)
    details = {
        "task_count": task_count,
        "max_tasks_per_plan": MAX_TASKS_PER_PLAN,
        "over_budget_by": max(0, task_count - MAX_TASKS_PER_PLAN),
    }
    if task_count > MAX_TASKS_PER_PLAN:
        return ValidationCheck.from_dict({
            "check_id": PLAN_CHECK_TASK_COUNT_BUDGET,
            "status": ValidationStatus.FAIL.value,
            "field": "tasks",
            "reason": "task plan proposal exceeds frozen max_tasks_per_plan",
            "details": details,
        })
    return ValidationCheck.from_dict({
        "check_id": PLAN_CHECK_TASK_COUNT_BUDGET,
        "status": ValidationStatus.PASS.value,
        "field": "tasks",
        "reason": None,
        "details": details,
    })


def _graph_preconditions(
    proposal: TaskPlanProposal,
) -> tuple[tuple[str, ...], tuple[dict[str, str], ...], tuple[str, ...]]:
    task_ids = tuple(task.task_id.value for task in proposal.tasks)
    seen: set[str] = set()
    duplicate_ids: list[str] = []
    duplicate_seen: set[str] = set()
    for task_id in task_ids:
        if task_id in seen and task_id not in duplicate_seen:
            duplicate_ids.append(task_id)
            duplicate_seen.add(task_id)
        seen.add(task_id)

    known_ids = set(task_ids)
    missing: list[dict[str, str]] = []
    dependencies: dict[str, tuple[str, ...]] = {}
    for task in proposal.tasks:
        task_id = task.task_id.value
        dependencies[task_id] = tuple(dep.value for dep in task.dependencies)
        for dependency in task.dependencies:
            if dependency.value not in known_ids:
                missing.append({
                    "task_id": task_id,
                    "dependency_task_id": dependency.value,
                })

    if duplicate_ids or missing:
        return tuple(duplicate_ids), tuple(missing), ()

    cycle = _find_cycle(dependencies)
    if cycle is not None:
        return (), (), tuple(item.value for item in cycle.task_ids)
    return (), (), ()


def _dependency_depth(proposal: TaskPlanProposal) -> tuple[int, tuple[str, ...]]:
    """Return longest dependency depth and one deterministic witness path.

    A root task has depth 0. A task depending directly on a root has depth 1.
    Therefore the frozen max_dependency_depth=12 permits at most 12 dependency
    edges in any path. The proposal is assumed to have unique IDs, existing
    dependencies, and no cycles.
    """

    dependencies = {
        task.task_id.value: tuple(dep.value for dep in task.dependencies)
        for task in proposal.tasks
    }
    dependents: dict[str, list[str]] = {task_id: [] for task_id in dependencies}
    remaining = {task_id: len(deps) for task_id, deps in dependencies.items()}
    depth = {task_id: 0 for task_id in dependencies}
    witness = {task_id: (task_id,) for task_id in dependencies}

    for task_id, deps in dependencies.items():
        for dep in deps:
            dependents[dep].append(task_id)
    for values in dependents.values():
        values.sort()

    ready = [task_id for task_id, count in remaining.items() if count == 0]
    heapq.heapify(ready)
    visited = 0
    while ready:
        task_id = heapq.heappop(ready)
        visited += 1
        for dependent in dependents[task_id]:
            candidate_depth = depth[task_id] + 1
            candidate_witness = witness[task_id] + (dependent,)
            if candidate_depth > depth[dependent] or (
                candidate_depth == depth[dependent]
                and candidate_witness < witness[dependent]
            ):
                depth[dependent] = candidate_depth
                witness[dependent] = candidate_witness
            remaining[dependent] -= 1
            if remaining[dependent] == 0:
                heapq.heappush(ready, dependent)

    if visited != len(dependencies):
        raise PlanValidatorError(
            "dependency depth requires an acyclic proposal graph"
        )

    max_depth = max(depth.values(), default=0)
    candidates = sorted(
        witness[task_id]
        for task_id, value in depth.items()
        if value == max_depth
    )
    return max_depth, (() if not candidates else candidates[0])


def _dependency_depth_budget_check(proposal: TaskPlanProposal) -> ValidationCheck:
    duplicate_ids, missing, cycle = _graph_preconditions(proposal)
    base_details = {
        "max_dependency_depth": MAX_DEPENDENCY_DEPTH,
        "blocked_by_duplicate_task_ids": duplicate_ids,
        "blocked_by_missing_dependencies": missing,
        "blocked_by_cycle": cycle,
    }
    if duplicate_ids or missing or cycle:
        return ValidationCheck.from_dict({
            "check_id": PLAN_CHECK_DEPENDENCY_DEPTH_BUDGET,
            "status": ValidationStatus.FAIL.value,
            "field": "tasks[].dependencies",
            "reason": "dependency depth cannot be established until graph validation errors are fixed",
            "details": {
                **base_details,
                "observed_dependency_depth": None,
                "deepest_path": (),
            },
        })

    observed_depth, deepest_path = _dependency_depth(proposal)
    details = {
        **base_details,
        "observed_dependency_depth": observed_depth,
        "deepest_path": deepest_path,
    }
    if observed_depth > MAX_DEPENDENCY_DEPTH:
        return ValidationCheck.from_dict({
            "check_id": PLAN_CHECK_DEPENDENCY_DEPTH_BUDGET,
            "status": ValidationStatus.FAIL.value,
            "field": "tasks[].dependencies",
            "reason": "task plan proposal exceeds frozen max_dependency_depth",
            "details": details,
        })
    return ValidationCheck.from_dict({
        "check_id": PLAN_CHECK_DEPENDENCY_DEPTH_BUDGET,
        "status": ValidationStatus.PASS.value,
        "field": "tasks[].dependencies",
        "reason": None,
        "details": details,
    })


def _retry_budget_check(proposal: TaskPlanProposal) -> ValidationCheck:
    over_budget: list[dict[str, int | str]] = []
    for task in proposal.tasks:
        if task.max_attempts > MAX_TASK_ATTEMPTS:
            over_budget.append({
                "task_id": task.task_id.value,
                "max_attempts": task.max_attempts,
            })
    details = {
        "task_count": len(proposal.tasks),
        "max_attempts_per_task": MAX_TASK_ATTEMPTS,
        "over_budget_tasks": over_budget,
    }
    if over_budget:
        return ValidationCheck.from_dict({
            "check_id": PLAN_CHECK_RETRY_BUDGET,
            "status": ValidationStatus.FAIL.value,
            "field": "tasks[].max_attempts",
            "reason": "task plan proposal exceeds frozen max_attempts policy",
            "details": details,
        })
    return ValidationCheck.from_dict({
        "check_id": PLAN_CHECK_RETRY_BUDGET,
        "status": ValidationStatus.PASS.value,
        "field": "tasks[].max_attempts",
        "reason": None,
        "details": details,
    })


def _runtime_resource_budget_check(proposal: TaskPlanProposal) -> ValidationCheck:
    del proposal  # This check verifies frozen runtime limits, not plan fields.
    observed = {
        "max_parallel_agents": MAX_PARALLEL_AGENT_TASKS,
        "max_loaded_models": MAX_LOADED_MODELS,
        "task_timeout_s": TASK_TIMEOUT_S,
    }
    expected = {
        "max_parallel_agents": FROZEN_MAX_PARALLEL_AGENTS,
        "max_loaded_models": FROZEN_MAX_LOADED_MODELS,
        "task_timeout_s": FROZEN_TASK_TIMEOUT_S,
    }
    mismatches = {
        key: {"observed": observed[key], "expected": expected[key]}
        for key in expected
        if observed[key] != expected[key]
    }
    details = {
        "observed": observed,
        "expected": expected,
        "mismatches": mismatches,
        "proposal_resource_overrides": False,
    }
    if mismatches:
        return ValidationCheck.from_dict({
            "check_id": PLAN_CHECK_RUNTIME_RESOURCE_BUDGET,
            "status": ValidationStatus.FAIL.value,
            "field": "runtime_resource_policy",
            "reason": "runtime resource policy does not match frozen V8 limits",
            "details": details,
        })
    return ValidationCheck.from_dict({
        "check_id": PLAN_CHECK_RUNTIME_RESOURCE_BUDGET,
        "status": ValidationStatus.PASS.value,
        "field": "runtime_resource_policy",
        "reason": None,
        "details": details,
    })


@dataclass(frozen=True, slots=True)
class PlanValidator:
    """Deterministic Phase-8 validator for ``TaskPlanProposal``.

    O8.3 established the validation boundary and the unique-task-ID check.
    O8.4 adds RoleRegistry membership. O8.5 adds OutputSchemaRegistry
    membership. O8.6 adds dependency existence, self-dependency, and DAG
    acyclicity checks. O8.7 adds frozen task-count, dependency-depth, retry,
    and runtime resource-budget checks. O8.8 remains the persistence owner.
    This validator does not approve, persist, schedule, or execute plans.
    """

    contract_version: str = PLAN_VALIDATOR_CONTRACT_VERSION
    role_registry: RoleRegistry = DEFAULT_ROLE_REGISTRY
    output_schema_registry: OutputSchemaRegistry = DEFAULT_OUTPUT_SCHEMA_REGISTRY

    def __post_init__(self) -> None:
        if self.contract_version != PLAN_VALIDATOR_CONTRACT_VERSION:
            raise PlanValidatorError(
                "contract_version must match PLAN_VALIDATOR_CONTRACT_VERSION"
            )
        if not isinstance(self.role_registry, RoleRegistry):
            raise PlanValidatorError("role_registry must be a RoleRegistry")
        if not isinstance(self.output_schema_registry, OutputSchemaRegistry):
            raise PlanValidatorError(
                "output_schema_registry must be an OutputSchemaRegistry"
            )

    @property
    def check_ids(self) -> tuple[str, ...]:
        return PLAN_VALIDATOR_CHECK_IDS

    def validate(self, proposal: TaskPlanProposal) -> ValidationResult:
        if not isinstance(proposal, TaskPlanProposal):
            raise PlanValidatorError("proposal must be a TaskPlanProposal")

        checks = (
            _unique_task_ids_check(proposal),
            _known_roles_check(proposal, self.role_registry),
            _known_output_schemas_check(proposal, self.output_schema_registry),
            _dependency_existence_check(proposal),
            _no_self_dependencies_check(proposal),
            _dag_acyclic_check(proposal),
            _task_count_budget_check(proposal),
            _dependency_depth_budget_check(proposal),
            _retry_budget_check(proposal),
            _runtime_resource_budget_check(proposal),
        )
        return ValidationResult.from_checks(checks)


DEFAULT_PLAN_VALIDATOR = PlanValidator()


def validate_task_plan_proposal(
    proposal: TaskPlanProposal,
    *,
    validator: PlanValidator = DEFAULT_PLAN_VALIDATOR,
) -> ValidationResult:
    """Validate a TaskPlanProposal using the deterministic Phase-8 framework."""

    if not isinstance(validator, PlanValidator):
        raise TypeError("validator must be a PlanValidator")
    return validator.validate(proposal)
