from __future__ import annotations

from pathlib import Path

import pytest

from src.capabilities.catalog import NAV001, build_default_registry
from src.capabilities.navigation.go_to_target import GoToTargetModule
from src.core.capability_definition import CapabilityDefinition
from src.verification.goto_target_verifier import GoToTargetVerifier


PROJECT = Path(__file__).resolve().parents[2]


def test_nav001_definition_matches_frozen_project_spec() -> None:
    spec = NAV001.validate_project(PROJECT)
    assert spec["spec_id"] == "NAV-001"
    assert NAV001.module_cls is GoToTargetModule
    assert NAV001.verifier_cls is GoToTargetVerifier
    assert NAV001.spec_path(PROJECT).is_file()
    assert NAV001.source_path(PROJECT).is_file()


def test_default_registry_exposes_nav001_definition() -> None:
    registry = build_default_registry(PROJECT)
    definition = registry.get_definition("NAV-001")
    assert definition is NAV001
    assert registry.get("NAV-001") is GoToTargetModule
    assert registry.describe()[0]["capability_id"] == "NAV-001"


def test_definition_rejects_empty_id() -> None:
    with pytest.raises(ValueError, match="capability_id"):
        CapabilityDefinition(
            capability_id=" ",
            name="bad",
            module_cls=GoToTargetModule,
            verifier_cls=GoToTargetVerifier,
            spec_relpath="specs/NAV-001.yaml",
            source_relpath="src/capabilities/navigation/go_to_target.py",
        )
