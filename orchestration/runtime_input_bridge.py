from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from src.evidence.attempt import (
    AttemptEvidence,
)
from user_interface.input_builder import (
    InputBuildError,
    build_input_artifacts,
)
from user_interface.input_schema import (
    CanonicalExperimentInput,
    UserRuntimeInput,
)
from user_interface.input_validation import (
    RuntimeInputValidator,
)
from user_interface.policy import (
    load_input_policy,
)


@dataclass(frozen=True)
class PreparedAttempt:
    attempt_number: int
    attempt_dir: Path
    canonical_input: CanonicalExperimentInput


def next_attempt_number(
    *,
    runs_root: Path,
    task_id: str,
    scenario_id: str,
) -> int:
    base = (
        Path(runs_root)
        / task_id
        / scenario_id
    )

    if not base.exists():
        return 1

    used: list[int] = []

    for p in base.glob(
        "attempt_*"
    ):
        if not p.is_dir():
            continue

        suffix = p.name.removeprefix(
            "attempt_"
        )

        try:
            used.append(int(suffix))
        except ValueError:
            continue

    return (
        max(used) + 1
        if used
        else 1
    )


def prepare_runtime_attempt(
    *,
    runtime_input: UserRuntimeInput,
    policy_path: Path,
    runs_root: Path,
    task_id: str = "NAV-001",
) -> PreparedAttempt:
    policy = load_input_policy(
        policy_path
    )

    validator = RuntimeInputValidator(
        supported_frames=set(
            policy.supported_frames
        ),
        test_area=policy.test_area,
    )

    attempt_number = next_attempt_number(
        runs_root=runs_root,
        task_id=task_id,
        scenario_id=(
            runtime_input.scenario_id
        ),
    )

    evidence = AttemptEvidence(
        root=runs_root,
        task_id=task_id,
        scenario_id=(
            runtime_input.scenario_id
        ),
        attempt=attempt_number,
    )

    evidence.create()

    evidence.write_metadata(
        {
            "phase":
                "runtime_input_preparation",
            "input_policy":
                str(policy_path),
            "input_policy_status":
                policy.status,
            "input_policy_version":
                policy.version,
        }
    )

    evidence.append_event(
        "runtime_input_received"
    )

    try:
        canonical = (
            build_input_artifacts(
                runtime_input=runtime_input,
                validator=validator,
                artifact_dir=evidence.path,
                task_id=task_id,
            )
        )

    except InputBuildError as exc:
        evidence.append_event(
            "runtime_input_rejected",
            {
                "failure_code":
                    "INVALID_INPUT",
                "errors":
                    list(
                        exc.result.errors
                    ),
            },
        )

        # Invalid attempts are complete:
        # there will be no simulator execution.
        evidence.finalize()

        raise

    evidence.append_event(
        "runtime_input_validated",
        {
            "frame":
                canonical.frame,
            "scenario_id":
                canonical.scenario_id,
        },
    )

    return PreparedAttempt(
        attempt_number=attempt_number,
        attempt_dir=evidence.path,
        canonical_input=canonical,
    )
