from __future__ import annotations

import argparse
from dataclasses import dataclass, asdict
from fnmatch import fnmatch
from pathlib import Path
from typing import Iterable

import yaml


@dataclass(frozen=True)
class BoundaryResult:
    role: str
    passed: bool
    changed_files: list[str]
    violations: list[str]

    def to_dict(self):
        return asdict(self)


def _matches(path: str, pattern: str) -> bool:
    # fnmatch's ** support is sufficient for our repository-relative policy paths.
    return fnmatch(path, pattern) or (pattern.endswith('/**') and path.startswith(pattern[:-3]))


def check_changed_files(role: str, changed_files: Iterable[str], policy_path: Path) -> BoundaryResult:
    policy = yaml.safe_load(policy_path.read_text())
    try:
        role_policy = policy["roles"][role]
    except KeyError as exc:
        raise ValueError(f"Unknown role in boundary policy: {role}") from exc

    writable = role_policy.get("writable", []) + role_policy.get("limited_writable", [])
    forbidden = role_policy.get("forbidden", [])
    normalized = [Path(p).as_posix().lstrip("./") for p in changed_files]
    violations: list[str] = []

    for path in normalized:
        if any(_matches(path, pat) for pat in forbidden):
            violations.append(f"FORBIDDEN:{path}")
            continue
        if not any(_matches(path, pat) for pat in writable):
            violations.append(f"OUTSIDE_WRITABLE_SCOPE:{path}")

    return BoundaryResult(role, not violations, normalized, violations)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--role", required=True)
    parser.add_argument("--policy", default="config/boundaries.yaml")
    parser.add_argument("files", nargs="+")
    args = parser.parse_args()
    result = check_changed_files(args.role, args.files, Path(args.policy))
    print(yaml.safe_dump(result.to_dict(), sort_keys=False))
    return 0 if result.passed else 2


if __name__ == "__main__":
    raise SystemExit(main())
