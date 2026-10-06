from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from src.core.base_capability import BaseCapabilityModule
from src.core.capability_definition import CapabilityDefinition
from src.core.executor import CapabilityExecutor
from src.core.registry import CapabilityRegistry


class DummyModule(BaseCapabilityModule):
    def __init__(self, adapter: Any, *, log_hook=None) -> None:
        super().__init__(log_hook=log_hook)
        self.adapter = adapter

    async def validate_input(self, input_data: dict[str, Any]) -> None:
        return None

    async def check_preconditions(self, input_data: dict[str, Any]) -> None:
        return None

    async def execute(self, input_data: dict[str, Any]) -> Any:
        return {"adapter": self.adapter}

    async def build_result(
        self,
        input_data: dict[str, Any],
        execution_output: Any,
        elapsed_time_s: float,
    ) -> dict[str, Any]:
        return {"adapter": execution_output["adapter"]}


@dataclass(frozen=True)
class DummyVerification:
    overall: str = "PASS"

    def to_dict(self) -> dict[str, Any]:
        return {"overall": self.overall}


class DummyVerifier:
    def __init__(self, spec_path: Path) -> None:
        self.spec_path = Path(spec_path)

    def verify_attempt(self, attempt_dir: Path, write_result: bool = True) -> DummyVerification:
        return DummyVerification()


def make_definition() -> CapabilityDefinition:
    return CapabilityDefinition(
        capability_id="TEST-001",
        name="Dummy",
        module_cls=DummyModule,
        verifier_cls=DummyVerifier,
        spec_relpath="specs/TEST-001.yaml",
        source_relpath="src/dummy.py",
    )


def test_registry_rejects_duplicate_definition() -> None:
    registry = CapabilityRegistry()
    definition = make_definition()
    registry.register_definition(definition)
    with pytest.raises(ValueError, match="already registered"):
        registry.register_definition(definition)


def test_legacy_registration_requires_definition_for_generic_execution() -> None:
    registry = CapabilityRegistry()
    registry.register("legacy", DummyModule)
    assert registry.get("legacy") is DummyModule
    with pytest.raises(KeyError, match="legacy module registration"):
        registry.get_definition("legacy")


def test_executor_selects_module_from_definition(tmp_path: Path) -> None:
    registry = CapabilityRegistry()
    registry.register_definition(make_definition())
    executor = CapabilityExecutor(registry, tmp_path)
    result = asyncio.run(
        executor.execute(
            "TEST-001",
            adapter="adapter-token",
            input_data={"timeout_s": 1.0},
        )
    )
    assert result.status_value == "completed"
    assert result.data["adapter"] == "adapter-token"


def test_executor_selects_deterministic_verifier(tmp_path: Path) -> None:
    (tmp_path / "specs").mkdir()
    (tmp_path / "specs/TEST-001.yaml").write_text("spec_id: TEST-001\n", encoding="utf-8")
    registry = CapabilityRegistry()
    registry.register_definition(make_definition())
    executor = CapabilityExecutor(registry, tmp_path)
    result = executor.verify_attempt("TEST-001", tmp_path / "attempt")
    assert result.overall == "PASS"
