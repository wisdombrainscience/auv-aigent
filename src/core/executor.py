from __future__ import annotations

from pathlib import Path
from typing import Any

from src.core.base_capability import LogHook
from src.core.registry import CapabilityRegistry
from src.core.result import CapabilityResult
from src.verification.base import VerificationResultProtocol


class CapabilityExecutor:
    """Generic capability runtime entry point.

    This layer owns component selection only.  Flight behavior remains inside
    the capability module and PASS/FAIL remains inside its deterministic
    verifier.
    """

    def __init__(self, registry: CapabilityRegistry, project: Path) -> None:
        self.registry = registry
        self.project = Path(project).resolve()

    async def execute(
        self,
        capability_id: str,
        *,
        adapter: Any,
        input_data: dict[str, Any],
        log_hook: LogHook | None = None,
        module_kwargs: dict[str, Any] | None = None,
    ) -> CapabilityResult:
        definition = self.registry.get_definition(capability_id)
        module = definition.create_module(
            adapter,
            log_hook=log_hook,
            **(module_kwargs or {}),
        )
        return await module.run(input_data)

    def verify_attempt(
        self,
        capability_id: str,
        attempt_dir: Path,
        *,
        write_result: bool = True,
    ) -> VerificationResultProtocol:
        definition = self.registry.get_definition(capability_id)
        verifier = definition.create_verifier(self.project)
        return verifier.verify_attempt(
            Path(attempt_dir),
            write_result=write_result,
        )
