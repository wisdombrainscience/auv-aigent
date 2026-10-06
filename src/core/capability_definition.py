from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Type

import yaml

from src.core.base_capability import BaseCapabilityModule
from src.verification.base import CapabilityVerifier


@dataclass(frozen=True)
class CapabilityDefinition:
    """Declarative binding between a frozen spec and runtime components.

    The definition intentionally contains no flight logic.  It tells the
    framework where the authoritative specification lives and which module and
    deterministic verifier implement that capability.
    """

    capability_id: str
    name: str
    module_cls: Type[BaseCapabilityModule]
    verifier_cls: type
    spec_relpath: str
    source_relpath: str
    description: str = ""

    def __post_init__(self) -> None:
        capability_id = self.capability_id.strip()
        name = self.name.strip()
        if not capability_id:
            raise ValueError("capability_id must not be empty")
        if not name:
            raise ValueError("name must not be empty")
        if not issubclass(self.module_cls, BaseCapabilityModule):
            raise TypeError("module_cls must inherit BaseCapabilityModule")
        if not self.spec_relpath.strip():
            raise ValueError("spec_relpath must not be empty")
        if not self.source_relpath.strip():
            raise ValueError("source_relpath must not be empty")

    def spec_path(self, project: Path) -> Path:
        return Path(project).resolve() / self.spec_relpath

    def source_path(self, project: Path) -> Path:
        return Path(project).resolve() / self.source_relpath

    def create_module(
        self,
        adapter: Any,
        *,
        log_hook: Any = None,
        **kwargs: Any,
    ) -> BaseCapabilityModule:
        return self.module_cls(
            adapter,
            log_hook=log_hook,
            **kwargs,
        )

    def create_verifier(self, project: Path) -> CapabilityVerifier:
        verifier = self.verifier_cls(self.spec_path(project))
        if not isinstance(verifier, CapabilityVerifier):
            raise TypeError(
                f"{self.verifier_cls.__name__} does not satisfy CapabilityVerifier"
            )
        return verifier

    def validate_project(self, project: Path) -> dict[str, Any]:
        """Validate that the frozen spec agrees with the registered binding."""

        spec_path = self.spec_path(project)
        source_path = self.source_path(project)
        if not spec_path.is_file():
            raise FileNotFoundError(f"missing capability spec: {spec_path}")
        if not source_path.is_file():
            raise FileNotFoundError(f"missing capability source: {source_path}")

        raw = yaml.safe_load(spec_path.read_text(encoding="utf-8")) or {}
        if raw.get("spec_id") != self.capability_id:
            raise ValueError(
                f"spec_id mismatch: expected {self.capability_id}, got {raw.get('spec_id')}"
            )
        if raw.get("status") != "frozen":
            raise ValueError(f"{self.capability_id} spec must be frozen")
        declared_module = raw.get("module")
        if declared_module and declared_module != self.module_cls.__name__:
            raise ValueError(
                "module mismatch: "
                f"spec declares {declared_module}, registry binds {self.module_cls.__name__}"
            )
        acceptance = raw.get("acceptance") or {}
        if acceptance.get("authoritative_verifier") != "independent_python_verifier":
            raise ValueError(
                f"{self.capability_id} must use independent_python_verifier"
            )
        return raw

    def to_dict(self) -> dict[str, Any]:
        return {
            "capability_id": self.capability_id,
            "name": self.name,
            "module": self.module_cls.__name__,
            "verifier": self.verifier_cls.__name__,
            "spec_relpath": self.spec_relpath,
            "source_relpath": self.source_relpath,
            "description": self.description,
        }
