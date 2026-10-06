from pathlib import Path

from src.capabilities.catalog import build_default_registry
from src.capabilities.navigation.return_home import ReturnHomeModule
from src.verification.return_home_verifier import ReturnHomeVerifier


PROJECT = Path(__file__).resolve().parents[2]


def test_return_home_is_registered_as_generic_capability() -> None:
    registry = build_default_registry(PROJECT)
    definition = registry.get_definition("RETURN-HOME-001")
    assert definition.module_cls is ReturnHomeModule
    assert definition.verifier_cls is ReturnHomeVerifier
    assert definition.spec_path(PROJECT).name == "RETURN-HOME-001.yaml"


def test_default_registry_contains_all_reference_capabilities() -> None:
    registry = build_default_registry(PROJECT)
    ids = {item["capability_id"] for item in registry.describe()}
    assert {
        "NAV-001",
        "TAKEOFF-001",
        "LAND-001",
        "RETURN-HOME-001",
    }.issubset(ids)
