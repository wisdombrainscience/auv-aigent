#!/usr/bin/env python3
from __future__ import annotations
import argparse
from pathlib import Path
import sys
_PROJECT_ROOT=Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path: sys.path.insert(0,str(_PROJECT_ROOT))
from ai.providers.ollama import DEFAULT_OLLAMA_ENDPOINT,OllamaModelListError,OllamaProvider,OllamaProviderConfigurationError

def main():
    p=argparse.ArgumentParser(description='Bounded local-only Ollama model-list probe'); p.add_argument('--endpoint',default=DEFAULT_OLLAMA_ENDPOINT); p.add_argument('--timeout',type=float,default=2.0); a=p.parse_args()
    try: models=OllamaProvider(endpoint=a.endpoint,health_timeout_s=a.timeout).list_models()
    except OllamaProviderConfigurationError as exc: print(f'OLLAMA_MODELS=CONFIG_ERROR detail={exc}'); return 2
    except OllamaModelListError as exc: print(f'OLLAMA_MODELS=ERROR detail={exc}'); return 3
    names=[m.name for m in models.models]; print(f'OLLAMA_MODELS=PASS count={len(names)}')
    for name in names: print(f'MODEL={name}')
    return 0
if __name__=='__main__': raise SystemExit(main())
