from dataclasses import dataclass, asdict
from typing import Any


@dataclass(frozen=True)
class FailurePackage:
    failure_code: str
    reason: str
    attempt_id: str
    evidence: dict[str, Any]
    recovery_eligible: bool

    def to_dict(self):
        return asdict(self)
