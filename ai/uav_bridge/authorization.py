from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from ai.contracts.validation_result import (
    ValidationCheck,
    ValidationResult,
    ValidationStatus,
)
from ai.uav_bridge.proposal import SimulationProposal
from ai.uav_bridge.validation import validate_bridge_proposal


CAPABILITY_ALLOWLIST_CONTRACT_VERSION = "1.0"

# Security policy, intentionally independent from the runtime catalog. Adding a
# capability to the existing runtime must never grant the AI Bridge permission
# automatically. Any future capability needs an explicit reviewed allowlist
# change.
BRIDGE_CAPABILITY_ALLOWLIST = (
    "TAKEOFF-001",
    "NAV-001",
    "RETURN-HOME-001",
    "LAND-001",
)
_BRIDGE_CAPABILITY_ALLOWSET = frozenset(BRIDGE_CAPABILITY_ALLOWLIST)


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


def _pass(
    check_id: str,
    *,
    field: str | None = None,
    details: Mapping[str, Any] | None = None,
) -> ValidationCheck:
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


def _observed_capabilities(proposal: SimulationProposal) -> tuple[str, ...]:
    raw = proposal.to_dict()["mission_payload"]
    if not isinstance(raw, dict):
        raise ValueError("mission_payload must be an object")
    steps = raw.get("steps")
    if not isinstance(steps, list) or not steps:
        raise ValueError("mission_payload.steps must be a non-empty array")

    observed: list[str] = []
    for index, step in enumerate(steps):
        if not isinstance(step, dict):
            raise ValueError(f"mission_payload.steps[{index}] must be an object")
        capability_id = step.get("capability_id")
        if (
            not isinstance(capability_id, str)
            or not capability_id
            or capability_id != capability_id.strip()
        ):
            raise ValueError(
                f"mission_payload.steps[{index}].capability_id must be clean non-empty text"
            )
        observed.append(capability_id)
    return tuple(observed)


def authorize_bridge_capabilities(
    project_root: Path,
    proposal: SimulationProposal,
) -> ValidationResult:
    """Authorize proposal capabilities at the U11.4 bridge boundary.

    Authorization is deny-by-default and uses a frozen explicit allowlist. The
    runtime capability registry is deliberately *not* treated as authorization:
    a capability becoming available in the runtime does not make it available
    to the AI Bridge. U11.3 deterministic bridge validation must also pass for
    the authorization result to pass.

    This function is read-only. It does not authorize a mission (U11.5), invoke
    the runtime (U11.6), persist a UAV run (U11.7), touch PX4/MAVSDK, or own a
    simulation verdict.
    """

    if not isinstance(proposal, SimulationProposal):
        raise TypeError("proposal must be SimulationProposal")

    checks: list[ValidationCheck] = []

    bridge_result = validate_bridge_proposal(Path(project_root), proposal)
    failed_bridge_checks = [
        item.check_id
        for item in bridge_result.checks
        if item.status is ValidationStatus.FAIL
    ]
    if bridge_result.overall is ValidationStatus.PASS:
        checks.append(
            _pass(
                "u11_4.bridge_validation",
                field="mission_payload",
                details={
                    "u11_3_contract": "1.0",
                    "checks": len(bridge_result.checks),
                },
            )
        )
    else:
        checks.append(
            _fail(
                "u11_4.bridge_validation",
                field="mission_payload",
                reason="U11.3 bridge validation must pass before capability authorization can pass",
                details={
                    "u11_3_contract": "1.0",
                    "failed_check_ids": failed_bridge_checks,
                },
            )
        )

    try:
        observed = _observed_capabilities(proposal)
    except (TypeError, ValueError, KeyError) as exc:
        checks.append(
            _fail(
                "u11_4.capability_allowlist",
                field="mission_payload.steps",
                reason="capability IDs cannot be extracted safely; deny by default",
                details={
                    "allowed": list(BRIDGE_CAPABILITY_ALLOWLIST),
                    "deny_by_default": True,
                    "error": f"{type(exc).__name__}: {exc}",
                },
            )
        )
        return ValidationResult.from_checks(checks)

    denied = sorted(set(observed) - _BRIDGE_CAPABILITY_ALLOWSET)
    if denied:
        checks.append(
            _fail(
                "u11_4.capability_allowlist",
                field="mission_payload.steps[].capability_id",
                reason="one or more capability IDs are not authorized by the explicit UAV Bridge allowlist",
                details={
                    "allowed": list(BRIDGE_CAPABILITY_ALLOWLIST),
                    "observed": list(observed),
                    "denied": denied,
                    "deny_by_default": True,
                    "authorization_source": "explicit_static_allowlist",
                },
            )
        )
    else:
        checks.append(
            _pass(
                "u11_4.capability_allowlist",
                field="mission_payload.steps[].capability_id",
                details={
                    "allowed": list(BRIDGE_CAPABILITY_ALLOWLIST),
                    "observed": list(observed),
                    "denied": [],
                    "deny_by_default": True,
                    "authorization_source": "explicit_static_allowlist",
                },
            )
        )

    return ValidationResult.from_checks(checks)

MISSION_ALLOWLIST_CONTRACT_VERSION = "1.0"

# Mission authorization is intentionally independent from runtime support.
# A mission becoming available in the existing runtime must never grant the AI
# Bridge permission automatically. Any future mission requires an explicit
# reviewed allowlist change.
BRIDGE_MISSION_ALLOWLIST = ("MISSION-001",)
_BRIDGE_MISSION_ALLOWSET = frozenset(BRIDGE_MISSION_ALLOWLIST)


def authorize_bridge_mission(
    project_root: Path,
    proposal: SimulationProposal,
) -> ValidationResult:
    """Authorize the proposal mission at the U11.5 bridge boundary.

    U11.4 capability authorization must pass before this result can pass.
    Mission authorization itself is explicit and deny-by-default, and is
    intentionally independent from runtime mission registry/support.

    This function is read-only. It does not invoke the runtime (U11.6), persist
    a UAV run (U11.7), touch PX4/MAVSDK, or own a simulation verdict.
    """

    if not isinstance(proposal, SimulationProposal):
        raise TypeError("proposal must be SimulationProposal")

    checks: list[ValidationCheck] = []

    capability_result = authorize_bridge_capabilities(Path(project_root), proposal)
    failed_capability_checks = [
        item.check_id
        for item in capability_result.checks
        if item.status is ValidationStatus.FAIL
    ]
    if capability_result.overall is ValidationStatus.PASS:
        checks.append(
            _pass(
                "u11_5.capability_authorization",
                field="mission_payload.steps[].capability_id",
                details={
                    "u11_4_contract": CAPABILITY_ALLOWLIST_CONTRACT_VERSION,
                    "checks": len(capability_result.checks),
                },
            )
        )
    else:
        checks.append(
            _fail(
                "u11_5.capability_authorization",
                field="mission_payload.steps[].capability_id",
                reason="U11.4 capability authorization must pass before mission authorization can pass",
                details={
                    "u11_4_contract": CAPABILITY_ALLOWLIST_CONTRACT_VERSION,
                    "failed_check_ids": failed_capability_checks,
                },
            )
        )

    mission_id = proposal.mission_id
    if mission_id not in _BRIDGE_MISSION_ALLOWSET:
        checks.append(
            _fail(
                "u11_5.mission_allowlist",
                field="mission_id",
                reason="mission ID is not authorized by the explicit UAV Bridge mission allowlist",
                details={
                    "allowed": list(BRIDGE_MISSION_ALLOWLIST),
                    "observed": mission_id,
                    "denied": mission_id,
                    "deny_by_default": True,
                    "authorization_source": "explicit_static_allowlist",
                },
            )
        )
    else:
        checks.append(
            _pass(
                "u11_5.mission_allowlist",
                field="mission_id",
                details={
                    "allowed": list(BRIDGE_MISSION_ALLOWLIST),
                    "observed": mission_id,
                    "denied": None,
                    "deny_by_default": True,
                    "authorization_source": "explicit_static_allowlist",
                },
            )
        )

    return ValidationResult.from_checks(checks)

