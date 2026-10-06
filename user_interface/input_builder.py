from __future__ import annotations

import json
from pathlib import Path

from user_interface.input_schema import (
    CanonicalExperimentInput,
    UserRuntimeInput,
)
from user_interface.input_validation import (
    InputValidationResult,
    RuntimeInputValidator,
)


class InputBuildError(RuntimeError):
    def __init__(
        self,
        result: InputValidationResult,
    ) -> None:
        self.result = result

        super().__init__(
            "INVALID_INPUT: "
            + "; ".join(result.errors)
        )


def _write_json_no_overwrite(
    path: Path,
    data: dict,
) -> None:
    if path.exists():
        raise FileExistsError(
            f"Refusing to overwrite artifact: {path}"
        )

    path.write_text(
        json.dumps(
            data,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )


def build_input_artifacts(
    *,
    runtime_input: UserRuntimeInput,
    validator: RuntimeInputValidator,
    artifact_dir: Path,
    task_id: str,
) -> CanonicalExperimentInput:
    artifact_dir = Path(artifact_dir)

    artifact_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    raw = runtime_input.to_dict()

    _write_json_no_overwrite(
        artifact_dir / "raw_user_input.json",
        raw,
    )

    validation = validator.validate(
        runtime_input
    )

    _write_json_no_overwrite(
        artifact_dir / "input_validation.json",
        validation.to_dict(),
    )

    if not validation.valid:
        raise InputBuildError(validation)

    canonical = CanonicalExperimentInput(
        task_id=task_id,
        target_position_m=(
            float(
                runtime_input.target_position_m[0]
            ),
            float(
                runtime_input.target_position_m[1]
            ),
            float(
                runtime_input.target_position_m[2]
            ),
        ),
        position_tolerance_m=float(
            runtime_input.position_tolerance_m
        ),
        timeout_s=float(
            runtime_input.timeout_s
        ),
        frame=runtime_input.frame,
        scenario_id=runtime_input.scenario_id,
    )

    _write_json_no_overwrite(
        artifact_dir / "input.json",
        canonical.to_dict(),
    )

    return canonical
