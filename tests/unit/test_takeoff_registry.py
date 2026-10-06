from pathlib import Path

from src.capabilities.catalog import build_default_registry
from src.capabilities.flight.takeoff import TakeoffModule
from src.verification.takeoff_verifier import TakeoffVerifier


PROJECT = Path(__file__).resolve().parents[2]


def test_takeoff_is_registered_as_generic_capability() -> None:
    registry = build_default_registry(PROJECT)
    definition = registry.get_definition("TAKEOFF-001")
    assert definition.module_cls is TakeoffModule
    assert definition.verifier_cls is TakeoffVerifier
    assert definition.spec_path(PROJECT).name == "TAKEOFF-001.yaml"


def test_default_registry_contains_nav_and_takeoff() -> None:
    registry = build_default_registry(PROJECT)
    ids = {item["capability_id"] for item in registry.describe()}
    assert {"NAV-001", "TAKEOFF-001"}.issubset(ids)
