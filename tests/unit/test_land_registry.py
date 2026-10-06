from pathlib import Path

from src.capabilities.catalog import build_default_registry
from src.capabilities.flight.land import LandModule
from src.verification.land_verifier import LandVerifier


PROJECT = Path(__file__).resolve().parents[2]


def test_land_is_registered_as_generic_capability() -> None:
    registry = build_default_registry(PROJECT)
    definition = registry.get_definition("LAND-001")
    assert definition.module_cls is LandModule
    assert definition.verifier_cls is LandVerifier
    assert definition.spec_path(PROJECT).name == "LAND-001.yaml"


def test_default_registry_contains_nav_takeoff_and_land() -> None:
    registry = build_default_registry(PROJECT)
    ids = {item["capability_id"] for item in registry.describe()}
    assert {"NAV-001", "TAKEOFF-001", "LAND-001"}.issubset(ids)
