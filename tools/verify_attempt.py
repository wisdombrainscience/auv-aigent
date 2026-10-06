import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from src.verification.goto_target_verifier import (
    GoToTargetVerifier,
)


if len(sys.argv) != 2:
    raise SystemExit(
        "Usage: python tools/verify_attempt.py "
        "<attempt_dir>"
    )

attempt_dir = Path(sys.argv[1])

verifier = GoToTargetVerifier(
    PROJECT_ROOT / "specs" / "NAV-001.yaml"
)

result = verifier.verify_attempt(
    attempt_dir
)

print(
    json.dumps(
        result.to_dict(),
        indent=2,
    )
)
