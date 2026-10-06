from __future__ import annotations

from pathlib import Path
from typing import Type

from src.core.base_capability import BaseCapabilityModule
from src.core.capability_definition import CapabilityDefinition


class CapabilityRegistry:
    """Registry of capability definitions with backward-compatible class access."""

    def __init__(self) -> None:
        # ``_items`` is kept for compatibility with the original skeleton and
        # existing callers that registered only module classes.
        self._items: dict[str, Type[BaseCapabilityModule]] = {}
        self._definitions: dict[str, CapabilityDefinition] = {}

    def register(self, name: str, module_cls: Type[BaseCapabilityModule]) -> None:
        if name in self._items or name in self._definitions:
            raise ValueError(f"Capability already registered: {name}")
        self._items[name] = module_cls

    def register_definition(self, definition: CapabilityDefinition) -> None:
        capability_id = definition.capability_id
        if capability_id in self._definitions or capability_id in self._items:
            raise ValueError(f"Capability already registered: {capability_id}")
        self._definitions[capability_id] = definition

    def get(self, name: str) -> Type[BaseCapabilityModule]:
        if name in self._definitions:
            return self._definitions[name].module_cls
        try:
            return self._items[name]
        except KeyError as exc:
            raise KeyError(f"Unknown capability: {name}") from exc

    def get_definition(self, capability_id: str) -> CapabilityDefinition:
        try:
            return self._definitions[capability_id]
        except KeyError as exc:
            if capability_id in self._items:
                raise KeyError(
                    f"Capability {capability_id} has only a legacy module registration; "
                    "register a CapabilityDefinition for generic execution"
                ) from exc
            raise KeyError(f"Unknown capability: {capability_id}") from exc

    def definitions(self) -> tuple[CapabilityDefinition, ...]:
        # Preserve explicit catalog registration order.  NAV-001 remains the
        # reference capability at index 0 while later capabilities are appended
        # deterministically by the catalog.  Python dict insertion order is
        # stable and avoids alphabetic reordering as the catalog grows.
        return tuple(self._definitions.values())

    def describe(self) -> list[dict[str, object]]:
        return [definition.to_dict() for definition in self.definitions()]

    def validate_project(self, project: Path) -> None:
        for definition in self.definitions():
            definition.validate_project(project)
