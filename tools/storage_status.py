#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.resources.manager import ResourceManager, load_resource_policy
from src.runtime_paths import runs_root


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", type=Path, default=ROOT)
    args = ap.parse_args()
    project = args.project.expanduser().resolve()
    policy = load_resource_policy(project / "config" / "resource_policy.yaml")
    status = ResourceManager(runs_root(project), policy).status()
    print(json.dumps(status, indent=2, sort_keys=True))
    return 0 if status["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
