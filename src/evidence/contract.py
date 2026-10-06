from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class EvidenceContractResult:
    complete: bool
    required: tuple[str, ...]
    missing: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "complete": self.complete,
            "required": list(self.required),
            "missing": list(self.missing),
        }


class EvidenceContractValidator:
    """Validate evidence completeness independently from content verification."""

    def __init__(self, spec_path: Path) -> None:
        self.spec_path = Path(spec_path)
        raw = yaml.safe_load(self.spec_path.read_text(encoding="utf-8")) or {}
        contract = raw.get("evidence_contract", {}) or {}
        required = contract.get("required", [])
        if not isinstance(required, list) or not all(isinstance(v, str) for v in required):
            raise ValueError("spec evidence_contract.required must be a string list")
        self.required = tuple(required)

    def validate(self, attempt_dir: Path) -> EvidenceContractResult:
        attempt_dir = Path(attempt_dir)
        missing = tuple(name for name in self.required if not (attempt_dir / name).is_file())
        return EvidenceContractResult(
            complete=not missing,
            required=self.required,
            missing=missing,
        )
