#!/usr/bin/env python3
from __future__ import annotations
import argparse
from pathlib import Path
import sys

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from ai.providers.contracts import GenerateRequest  # noqa: E402
from ai.providers.ollama import (  # noqa: E402
    DEFAULT_OLLAMA_ENDPOINT,
    DEFAULT_OLLAMA_GENERATE_TIMEOUT_S,
    OllamaGenerateError,
    OllamaProvider,
    OllamaProviderConfigurationError,
)


def main() -> int:
    p=argparse.ArgumentParser(description="One bounded local-only Ollama generation probe")
    p.add_argument("--model", required=True, help="Explicit installed local model name")
    p.add_argument("--endpoint", default=DEFAULT_OLLAMA_ENDPOINT)
    p.add_argument("--timeout", type=float, default=DEFAULT_OLLAMA_GENERATE_TIMEOUT_S)
    args=p.parse_args()

    schema={
        "type":"object",
        "properties":{"status":{"type":"string","const":"LOCAL_PROVIDER_OK"}},
        "required":["status"],
        "additionalProperties":False,
    }
    request=GenerateRequest(
        model=args.model,
        prompt="Return one JSON object whose status is LOCAL_PROVIDER_OK.",
        system_prompt="This is a local transport acceptance probe. Return only the requested JSON object.",
        output_schema=schema,
    )
    try:
        provider=OllamaProvider(
            endpoint=args.endpoint,
            generate_timeout_s=args.timeout,
        )
        result=provider.generate(request)
    except OllamaProviderConfigurationError as exc:
        print(f"OLLAMA_GENERATE=CONFIG_ERROR detail={exc}")
        return 2
    except OllamaGenerateError as exc:
        print(f"OLLAMA_GENERATE=ERROR detail={exc}")
        return 3

    size=len(result.content.encode("utf-8"))
    print(f"OLLAMA_GENERATE=PASS model={result.model} content_bytes={size}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
