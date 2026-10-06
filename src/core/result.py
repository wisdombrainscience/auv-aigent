from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class CapabilityStatus(str, Enum):
    CREATED = "created"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass(frozen=True)
class CapabilityResult:
    """
    Shared capability result model.

    Backward compatible with the original skeleton:

        CapabilityResult(
            task_id="T",
            status="created",
        )

    and with the newer lifecycle implementation:

        CapabilityResult(
            status=CapabilityStatus.COMPLETED,
            elapsed_time_s=1.23,
        )
    """

    task_id: str | None = None

    status: CapabilityStatus | str = (
        CapabilityStatus.CREATED
    )

    elapsed_time_s: float = 0.0

    data: dict[str, Any] = field(
        default_factory=dict
    )

    error_code: str | None = None
    reason: str | None = None

    lifecycle: tuple[str, ...] = ()

    @property
    def status_value(self) -> str:
        if isinstance(
            self.status,
            CapabilityStatus,
        ):
            return self.status.value

        return str(self.status)

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "status": self.status_value,
            "elapsed_time_s": self.elapsed_time_s,
            "data": self.data,
            "error_code": self.error_code,
            "reason": self.reason,
            "lifecycle": list(
                self.lifecycle
            ),
        }
