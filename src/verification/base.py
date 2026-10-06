from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class VerificationResultProtocol(Protocol):
    """Minimal result contract shared by deterministic capability verifiers."""

    overall: str

    def to_dict(self) -> dict[str, Any]:
        ...


@runtime_checkable
class CapabilityVerifier(Protocol):
    """Deterministic verifier interface for a capability evidence attempt."""

    def verify_attempt(
        self,
        attempt_dir: Path,
        write_result: bool = True,
    ) -> VerificationResultProtocol:
        ...
