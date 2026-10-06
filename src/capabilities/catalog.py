from __future__ import annotations

from pathlib import Path

from src.capabilities.flight.land import LandModule
from src.capabilities.flight.takeoff import TakeoffModule
from src.capabilities.navigation.go_to_target import GoToTargetModule
from src.capabilities.navigation.return_home import ReturnHomeModule
from src.core.capability_definition import CapabilityDefinition
from src.core.registry import CapabilityRegistry
from src.verification.goto_target_verifier import GoToTargetVerifier
from src.verification.land_verifier import LandVerifier
from src.verification.return_home_verifier import ReturnHomeVerifier
from src.verification.takeoff_verifier import TakeoffVerifier


NAV001 = CapabilityDefinition(
    capability_id="NAV-001",
    name="GoToTarget",
    module_cls=GoToTargetModule,
    verifier_cls=GoToTargetVerifier,
    spec_relpath="specs/NAV-001.yaml",
    source_relpath="src/capabilities/navigation/go_to_target.py",
    description=(
        "Reference local-NED navigation capability.  AI may assist engineering, "
        "but runtime verdict authority remains the independent Python verifier."
    ),
)

TAKEOFF001 = CapabilityDefinition(
    capability_id="TAKEOFF-001",
    name="Takeoff",
    module_cls=TakeoffModule,
    verifier_cls=TakeoffVerifier,
    spec_relpath="specs/TAKEOFF-001.yaml",
    source_relpath="src/capabilities/flight/takeoff.py",
    description=(
        "Verified simulation takeoff to a positive-up local altitude. "
        "The deterministic verifier checks altitude, horizontal drift, "
        "airborne transition, timeout and telemetry validity."
    ),
)


LAND001 = CapabilityDefinition(
    capability_id="LAND-001",
    name="Land",
    module_cls=LandModule,
    verifier_cls=LandVerifier,
    spec_relpath="specs/LAND-001.yaml",
    source_relpath="src/capabilities/flight/land.py",
    description=(
        "Verified simulation landing from airborne state to the local ground "
        "reference. The deterministic verifier checks touchdown altitude, "
        "horizontal drift, observed airborne-to-landed transition, timeout and "
        "telemetry validity."
    ),
)


RETURNHOME001 = CapabilityDefinition(
    capability_id="RETURN-HOME-001",
    name="ReturnHome",
    module_cls=ReturnHomeModule,
    verifier_cls=ReturnHomeVerifier,
    spec_relpath="specs/RETURN-HOME-001.yaml",
    source_relpath="src/capabilities/navigation/return_home.py",
    description=(
        "Verified airborne return to a declared LOCAL_NED home horizontal "
        "anchor at a requested return altitude. LAND-001 remains responsible "
        "for touchdown."
    ),
)


def build_default_registry(project: Path | None = None) -> CapabilityRegistry:
    registry = CapabilityRegistry()
    registry.register_definition(NAV001)
    registry.register_definition(TAKEOFF001)
    registry.register_definition(LAND001)
    registry.register_definition(RETURNHOME001)
    if project is not None:
        registry.validate_project(project)
    return registry
