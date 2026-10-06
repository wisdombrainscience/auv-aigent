# P2.4-H1 — Ollama Health Probe Entrypoint Hotfix

The original P2.4 runtime helper was documented as:

```bash
python3 tools/check_ollama_health.py
```

but Python placed `tools/` rather than the repository root at the front of
`sys.path`, so a clean direct invocation could fail before reaching Ollama with:

```text
ModuleNotFoundError: No module named 'ai'
```

The P2.4 provider implementation itself was healthy; the target confirmed this
by running the same probe with an explicit project root on `PYTHONPATH`, which
returned:

```text
OLLAMA_HEALTH=PASS provider=ollama detail=version=0.34.0
```

H1 fixes only the probe entrypoint. The script now derives its owning repository
root from `__file__` and adds that exact root to `sys.path` before importing the
local `ai` package.

No provider semantics, endpoint policy, retry behavior, Ollama API path,
contracts, or UAV execution code are changed.

The canonical acceptance command is again:

```bash
python3 tools/check_ollama_health.py
```
