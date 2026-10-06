from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

from user_interface.input_validation import (
    TestAreaBounds,
)


@dataclass(frozen=True)
class InputPolicy:
    version: int
    status: str
    supported_frames: frozenset[str]
    test_area: TestAreaBounds


def load_input_policy(
    path: Path,
) -> InputPolicy:
    path = Path(path)

    if not path.exists():
        raise FileNotFoundError(
            f"Input policy not found: {path}"
        )

    with path.open(
        "r",
        encoding="utf-8",
    ) as f:
        raw = yaml.safe_load(f)

    if not isinstance(raw, dict):
        raise ValueError(
            "Input policy must be a mapping"
        )

    version = raw.get("version")

    if version != 1:
        raise ValueError(
            f"Unsupported input policy version: "
            f"{version}"
        )

    status = raw.get("status")

    if not isinstance(status, str):
        raise ValueError(
            "Input policy status is required"
        )

    frames = raw.get(
        "supported_frames"
    )

    if (
        not isinstance(frames, list)
        or not frames
        or not all(
            isinstance(v, str)
            for v in frames
        )
    ):
        raise ValueError(
            "supported_frames must be "
            "a non-empty string list"
        )

    area = raw.get("test_area_m")

    # Prefer a canonical experiment-region source when configured.  The
    # legacy inline test_area_m form remains supported for tests and older
    # project snapshots.
    source = raw.get("test_area_source")
    if source is not None:
        if not isinstance(source, str) or not source.strip():
            raise ValueError("test_area_source must be a non-empty string")
        source_path = (path.parent / source).resolve()
        if not source_path.is_file():
            raise FileNotFoundError(f"Test-area source not found: {source_path}")
        source_raw = yaml.safe_load(source_path.read_text(encoding="utf-8")) or {}
        region = source_raw.get("permitted_test_region", {})
        try:
            area = {
                "x_min": region["x_m"]["min"],
                "x_max": region["x_m"]["max"],
                "y_min": region["y_m"]["min"],
                "y_max": region["y_m"]["max"],
                "z_min": region["z_m"]["min"],
                "z_max": region["z_m"]["max"],
            }
        except (KeyError, TypeError) as exc:
            raise ValueError("canonical experiment region is invalid") from exc

    if not isinstance(area, dict):
        raise ValueError(
            "test_area_m or test_area_source is required"
        )

    required = {
        "x_min",
        "x_max",
        "y_min",
        "y_max",
        "z_min",
        "z_max",
    }

    missing = required - set(area)

    if missing:
        raise ValueError(
            "Missing test-area fields: "
            + ",".join(sorted(missing))
        )

    bounds = TestAreaBounds(
        x_min=float(area["x_min"]),
        x_max=float(area["x_max"]),
        y_min=float(area["y_min"]),
        y_max=float(area["y_max"]),
        z_min=float(area["z_min"]),
        z_max=float(area["z_max"]),
    )

    return InputPolicy(
        version=version,
        status=status,
        supported_frames=frozenset(
            frames
        ),
        test_area=bounds,
    )
