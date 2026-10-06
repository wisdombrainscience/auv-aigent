#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path
import sys


# Allow the documented direct invocation:
#
#   python3 tools/check_ollama_health.py
#
# without requiring callers to export PYTHONPATH or install the package into the
# active interpreter first.  This bootstrap is intentionally limited to the
# repository root that owns this exact tool file.
_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from ai.providers.ollama import (  # noqa: E402
    DEFAULT_OLLAMA_ENDPOINT,
    OllamaProvider,
    OllamaProviderConfigurationError,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Bounded local-only Ollama health probe"
    )
    parser.add_argument(
        "--endpoint",
        default=DEFAULT_OLLAMA_ENDPOINT,
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=2.0,
    )
    args = parser.parse_args()

    try:
        provider = OllamaProvider(
            endpoint=args.endpoint,
            health_timeout_s=args.timeout,
        )
    except OllamaProviderConfigurationError as exc:
        print(f"OLLAMA_HEALTH=CONFIG_ERROR detail={exc}")
        return 2

    health = provider.health()
    if health.ok:
        print(
            "OLLAMA_HEALTH=PASS "
            f"provider={health.provider} "
            f"detail={health.detail}"
        )
        return 0

    print(
        "OLLAMA_HEALTH=UNAVAILABLE "
        f"provider={health.provider} "
        f"detail={health.detail}"
    )
    return 3


if __name__ == "__main__":
    raise SystemExit(main())
