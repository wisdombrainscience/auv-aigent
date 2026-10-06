#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path
import sys


_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from ai.model_manager import (  # noqa: E402
    ModelManager,
    ModelManagerError,
    ResourceMode,
)
from ai.providers.contracts import GenerateRequest  # noqa: E402
from ai.providers.errors import ProviderRuntimeError  # noqa: E402
from ai.providers.ollama import (  # noqa: E402
    DEFAULT_OLLAMA_ENDPOINT,
    OllamaProvider,
    OllamaProviderConfigurationError,
)
from ai.providers.retry import (  # noqa: E402
    ProviderRetryExhausted,
    RetryingLocalAIProvider,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Minimal local Model Manager acceptance probe"
    )
    parser.add_argument("--model", required=True)
    parser.add_argument("--endpoint", default=DEFAULT_OLLAMA_ENDPOINT)
    parser.add_argument("--health-timeout", type=float, default=2.0)
    parser.add_argument("--generate-timeout", type=float, default=60.0)
    args = parser.parse_args()

    try:
        provider = RetryingLocalAIProvider(
            OllamaProvider(
                endpoint=args.endpoint,
                health_timeout_s=args.health_timeout,
                generate_timeout_s=args.generate_timeout,
            )
        )
        manager = ModelManager(provider)

        health = manager.refresh_provider_health()
        if not health.ok:
            print(
                "MODEL_MANAGER=PROVIDER_UNHEALTHY "
                f"detail={health.detail}"
            )
            return 3

        manager.select_model(args.model)
        request = GenerateRequest(
            model=args.model,
            prompt="Reply with the single token OK.",
            system_prompt=None,
            output_schema=None,
        )
        response = manager.generate(request)
        snapshot = manager.snapshot()

    except OllamaProviderConfigurationError as exc:
        print(f"MODEL_MANAGER=CONFIG_ERROR detail={exc}")
        return 2
    except ProviderRetryExhausted as exc:
        print(
            "MODEL_MANAGER=PROVIDER_RETRY_EXHAUSTED "
            f"attempts={exc.attempts_used} code={exc.failure.code}"
        )
        return 4
    except ProviderRuntimeError as exc:
        print(
            "MODEL_MANAGER=PROVIDER_ERROR "
            f"code={exc.failure.code} reason={exc.reason}"
        )
        return 5
    except ModelManagerError as exc:
        print(f"MODEL_MANAGER=ERROR detail={exc}")
        return 6

    print(
        "MODEL_MANAGER=PASS "
        f"model={response.model} "
        f"content_bytes={len(response.content.encode('utf-8'))} "
        f"queue_depth={snapshot.pending_inferences} "
        f"resource_mode={snapshot.resource_mode.value} "
        f"lifecycle_supported={str(snapshot.lifecycle_supported).lower()}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
