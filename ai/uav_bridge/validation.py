from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from ai.contracts.validation_result import (
    ValidationCheck,
    ValidationResult,
    ValidationStatus,
)
from ai.uav_bridge.proposal import ExecutionScope, SimulationProposal
from src.mission.model import MissionPlan
from user_interface.policy import load_input_policy


BRIDGE_VALIDATION_CONTRACT_VERSION = "1.0"

MISSION_INPUT_POLICY_REL = Path("config/input_policy.yaml")

_MISSION_KEYS = frozenset(
    {
        "mission_id",
        "frame",
        "continuity_tolerance_m",
        "terminal_home_position_m",
        "terminal_home_tolerance_m",
        "steps",
    }
)
_STEP_KEYS = frozenset({"step_id", "capability_id", "post_action", "input"})

_CAPABILITY_INPUT_KEYS: dict[str, frozenset[str]] = {
    "TAKEOFF-001": frozenset(
        {
            "task_id",
            "frame",
            "target_altitude_m",
            "altitude_tolerance_m",
            "horizontal_tolerance_m",
            "timeout_s",
        }
    ),
    "NAV-001": frozenset(
        {
            "task_id",
            "frame",
            "target_position_m",
            "position_tolerance_m",
            "timeout_s",
        }
    ),
    "RETURN-HOME-001": frozenset(
        {
            "task_id",
            "frame",
            "home_position_m",
            "return_altitude_m",
            "horizontal_tolerance_m",
            "altitude_tolerance_m",
            "timeout_s",
        }
    ),
    "LAND-001": frozenset(
        {
            "task_id",
            "frame",
            "ground_altitude_tolerance_m",
            "horizontal_tolerance_m",
            "timeout_s",
        }
    ),
}

_EXPECTED_POST_ACTION = {
    "TAKEOFF-001": "hold",
    "NAV-001": "hold",
    "RETURN-HOME-001": "hold",
    "LAND-001": "none",
}


def _check(
    *,
    check_id: str,
    status: ValidationStatus,
    field: str | None = None,
    reason: str | None = None,
    details: Mapping[str, Any] | None = None,
) -> ValidationCheck:
    return ValidationCheck.from_dict(
        {
            "check_id": check_id,
            "status": status.value,
            "field": field,
            "reason": reason,
            "details": dict(details or {}),
        }
    )


def _pass(check_id: str, *, field: str | None = None, details: Mapping[str, Any] | None = None) -> ValidationCheck:
    return _check(
        check_id=check_id,
        status=ValidationStatus.PASS,
        field=field,
        details=details,
    )


def _fail(
    check_id: str,
    *,
    field: str | None,
    reason: str,
    details: Mapping[str, Any] | None = None,
) -> ValidationCheck:
    return _check(
        check_id=check_id,
        status=ValidationStatus.FAIL,
        field=field,
        reason=reason,
        details=details,
    )


def _strict_mission_payload(proposal: SimulationProposal) -> MissionPlan:
    raw = proposal.to_dict()["mission_payload"]
    if not isinstance(raw, dict):
        raise ValueError("mission_payload must be an object")
    if frozenset(raw) != _MISSION_KEYS:
        raise ValueError("mission_payload must contain exactly the MISSION-001 fields")
    if raw.get("mission_id") != proposal.mission_id:
        raise ValueError("proposal mission_id must match mission_payload.mission_id")

    steps = raw.get("steps")
    if not isinstance(steps, list):
        raise ValueError("mission_payload.steps must be an array")

    capabilities = [step.get("capability_id") for step in steps if isinstance(step, dict)]
    if len(capabilities) != len(steps):
        raise ValueError("every mission step must be an object")
    if len(steps) < 4:
        raise ValueError("MISSION-001 mapped plan must contain takeoff, navigation, return-home, and land")
    if capabilities[0] != "TAKEOFF-001":
        raise ValueError("MISSION-001 first capability must be TAKEOFF-001")
    if capabilities[-2:] != ["RETURN-HOME-001", "LAND-001"]:
        raise ValueError("MISSION-001 must end with RETURN-HOME-001 then LAND-001")
    if any(capability != "NAV-001" for capability in capabilities[1:-2]):
        raise ValueError("MISSION-001 middle capabilities must be NAV-001")
    if not capabilities[1:-2]:
        raise ValueError("MISSION-001 must contain at least one NAV-001 step")

    for index, step in enumerate(steps):
        if frozenset(step) != _STEP_KEYS:
            raise ValueError(f"steps[{index}] must contain exactly the mission step fields")
        capability_id = step["capability_id"]
        expected_keys = _CAPABILITY_INPUT_KEYS.get(capability_id)
        if expected_keys is None:
            raise ValueError(f"steps[{index}] has no U11.3 validation contract: {capability_id}")
        input_data = step["input"]
        if not isinstance(input_data, dict) or frozenset(input_data) != expected_keys:
            raise ValueError(f"steps[{index}].input does not match the {capability_id} contract")
        if input_data["task_id"] != capability_id:
            raise ValueError(f"steps[{index}].input.task_id must match capability_id")
        if input_data["frame"] != raw["frame"]:
            raise ValueError(f"steps[{index}].input.frame must match mission frame")
        if step["post_action"] != _EXPECTED_POST_ACTION[capability_id]:
            raise ValueError(f"steps[{index}].post_action is invalid for {capability_id}")

    # Existing deterministic mission model remains the runtime contract authority.
    return MissionPlan.from_dict(raw)


def _planned_targets(plan: MissionPlan) -> tuple[tuple[str, tuple[float, float, float]], ...]:
    home_n, home_e = plan.terminal_home_position_m
    targets: list[tuple[str, tuple[float, float, float]]] = [
        ("terminal_home", (float(home_n), float(home_e), 0.0)),
    ]

    for step in plan.steps:
        data = step.input_data
        if step.capability_id == "TAKEOFF-001":
            targets.append(
                (
                    step.step_id,
                    (
                        float(home_n),
                        float(home_e),
                        -float(data["target_altitude_m"]),
                    ),
                )
            )
        elif step.capability_id == "NAV-001":
            p = data["target_position_m"]
            if not isinstance(p, (list, tuple)) or len(p) != 3:
                raise ValueError(f"{step.step_id}.target_position_m must contain exactly three values")
            targets.append((step.step_id, (float(p[0]), float(p[1]), float(p[2]))))
        elif step.capability_id == "RETURN-HOME-001":
            p = data["home_position_m"]
            if not isinstance(p, (list, tuple)) or len(p) != 2:
                raise ValueError(f"{step.step_id}.home_position_m must contain exactly two values")
            targets.append(
                (
                    step.step_id,
                    (
                        float(p[0]),
                        float(p[1]),
                        -float(data["return_altitude_m"]),
                    ),
                )
            )
        elif step.capability_id == "LAND-001":
            targets.append((step.step_id, (float(home_n), float(home_e), 0.0)))

    return tuple(targets)


def validate_bridge_proposal(
    project_root: Path,
    proposal: SimulationProposal,
) -> ValidationResult:
    """Deterministically validate a simulation proposal before UAV authorization.

    U11.3 validates the existing Remote UAV mission contract and canonical
    simulation-input policy only. It does not authorize capabilities/missions,
    invoke a runtime, touch PX4/MAVSDK, persist evidence, or own a simulation
    verdict. Those responsibilities remain U11.4-U11.7 and the independent
    verifier.
    """

    if not isinstance(proposal, SimulationProposal):
        raise TypeError("proposal must be SimulationProposal")

    root = Path(project_root).resolve()
    checks: list[ValidationCheck] = []

    if proposal.execution_scope is not ExecutionScope.SIMULATION_ONLY:
        checks.append(
            _fail(
                "u11_3.execution_scope",
                field="execution_scope",
                reason="bridge accepts only SIMULATION_ONLY proposals",
                details={"observed": proposal.execution_scope.value},
            )
        )
        return ValidationResult.from_checks(checks)

    checks.append(
        _pass(
            "u11_3.execution_scope",
            field="execution_scope",
            details={"observed": "SIMULATION_ONLY"},
        )
    )

    try:
        plan = _strict_mission_payload(proposal)
    except (TypeError, ValueError, KeyError) as exc:
        checks.append(
            _fail(
                "u11_3.mission_contract",
                field="mission_payload",
                reason="proposal does not satisfy the deterministic MISSION-001 bridge contract",
                details={"error": f"{type(exc).__name__}: {exc}"},
            )
        )
        return ValidationResult.from_checks(checks)

    checks.append(
        _pass(
            "u11_3.mission_contract",
            field="mission_payload",
            details={
                "mission_id": plan.mission_id,
                "frame": plan.frame,
                "step_count": len(plan.steps),
            },
        )
    )

    policy_path = root / MISSION_INPUT_POLICY_REL
    try:
        policy = load_input_policy(policy_path)
    except Exception as exc:
        checks.append(
            _fail(
                "u11_3.runtime_policy",
                field="config/input_policy.yaml",
                reason="canonical runtime input policy is unavailable or invalid",
                details={"error": f"{type(exc).__name__}: {exc}"},
            )
        )
        return ValidationResult.from_checks(checks)

    if plan.frame not in policy.supported_frames:
        checks.append(
            _fail(
                "u11_3.runtime_policy",
                field="mission_payload.frame",
                reason="mission frame is not supported by canonical runtime policy",
                details={
                    "observed": plan.frame,
                    "supported_frames": sorted(policy.supported_frames),
                },
            )
        )
        return ValidationResult.from_checks(checks)

    checks.append(
        _pass(
            "u11_3.runtime_policy",
            field="config/input_policy.yaml",
            details={
                "policy_version": policy.version,
                "policy_status": policy.status,
                "frame": plan.frame,
            },
        )
    )

    try:
        targets = _planned_targets(plan)
    except (TypeError, ValueError, KeyError) as exc:
        checks.append(
            _fail(
                "u11_3.permitted_region",
                field="mission_payload.steps",
                reason="planned mission target geometry is invalid",
                details={"error": f"{type(exc).__name__}: {exc}"},
            )
        )
        return ValidationResult.from_checks(checks)

    outside = [
        {"target": name, "position_m": [x, y, z]}
        for name, (x, y, z) in targets
        if not policy.test_area.contains(x, y, z)
    ]
    if outside:
        checks.append(
            _fail(
                "u11_3.permitted_region",
                field="mission_payload",
                reason="one or more planned targets are outside the permitted simulation region",
                details={
                    "outside": outside,
                    "bounds": policy.test_area.to_dict(),
                },
            )
        )
        return ValidationResult.from_checks(checks)

    checks.append(
        _pass(
            "u11_3.permitted_region",
            field="mission_payload",
            details={
                "targets_checked": len(targets),
                "bounds": policy.test_area.to_dict(),
            },
        )
    )

    return ValidationResult.from_checks(checks)
