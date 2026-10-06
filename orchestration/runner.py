from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.core.base_capability import (
    BaseCapabilityModule,
)
from src.core.result import CapabilityResult
from src.evidence.attempt import (
    AttemptEvidence,
)


@dataclass(frozen=True)
class ExperimentRunSummary:
    attempt_dir: Path
    module_result: CapabilityResult

    def to_dict(self) -> dict[str, Any]:
        return {
            "attempt_dir": str(
                self.attempt_dir
            ),
            "module_result":
                self.module_result.to_dict(),
        }


class ExperimentRunner:
    """
    Deterministic experiment runner.

    Responsibilities at this stage:

    input.json
        ↓
    capability.run(...)
        ↓
    module_output.json

    Official verification is deliberately
    NOT performed by the capability.
    """

    def __init__(
        self,
        *,
        attempt_dir: Path,
        capability: BaseCapabilityModule,
    ) -> None:
        self.attempt_dir = Path(
            attempt_dir
        )

        self.capability = capability

        self.evidence = (
            self._evidence_from_path(
                self.attempt_dir
            )
        )

    @staticmethod
    def _evidence_from_path(
        attempt_dir: Path,
    ) -> AttemptEvidence:
        attempt_dir = Path(
            attempt_dir
        ).resolve()

        if not attempt_dir.name.startswith(
            "attempt_"
        ):
            raise ValueError(
                "Attempt directory must have "
                "name attempt_NNN"
            )

        try:
            attempt_number = int(
                attempt_dir.name
                .removeprefix(
                    "attempt_"
                )
            )
        except ValueError as exc:
            raise ValueError(
                "Invalid attempt directory name"
            ) from exc

        scenario_id = (
            attempt_dir.parent.name
        )

        task_id = (
            attempt_dir.parent.parent.name
        )

        runs_root = (
            attempt_dir
            .parent
            .parent
            .parent
        )

        return AttemptEvidence(
            root=runs_root,
            task_id=task_id,
            scenario_id=scenario_id,
            attempt=attempt_number,
        )

    def _load_input(
        self,
    ) -> dict[str, Any]:
        path = (
            self.attempt_dir
            / "input.json"
        )

        if not path.exists():
            raise FileNotFoundError(
                f"Missing canonical input: "
                f"{path}"
            )

        with path.open(
            "r",
            encoding="utf-8",
        ) as f:
            data = json.load(f)

        if not isinstance(
            data,
            dict,
        ):
            raise ValueError(
                "input.json must contain "
                "a JSON object"
            )

        return data

    async def run(
        self,
    ) -> ExperimentRunSummary:
        if self.evidence.is_finalized:
            raise RuntimeError(
                "Cannot execute a finalized "
                "attempt"
            )

        output_path = (
            self.attempt_dir
            / "module_output.json"
        )

        if output_path.exists():
            raise FileExistsError(
                "module_output.json already "
                "exists; refusing rerun in "
                "the same attempt"
            )

        input_data = (
            self._load_input()
        )

        self.evidence.append_event(
            "experiment_started",
            {
                "task_id":
                    input_data.get(
                        "task_id"
                    ),
                "scenario_id":
                    input_data.get(
                        "scenario_id"
                    ),
            },
        )

        result = await (
            self.capability.run(
                input_data
            )
        )

        self.evidence.write_json(
            "module_output.json",
            result.to_dict(),
        )

        self.evidence.append_event(
            "experiment_finished",
            {
                "module_status":
                    result.status_value,
                "error_code":
                    result.error_code,
            },
        )

        return ExperimentRunSummary(
            attempt_dir=self.attempt_dir,
            module_result=result,
        )
