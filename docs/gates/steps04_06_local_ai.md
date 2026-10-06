# STEP 04–06 — Local AI Environment

These steps must be executed on the actual development machine.
They cannot be marked PASS from a generated repository alone.

## STEP 04 — Install Ollama (Linux/Ubuntu)

Official current installation command:

```bash
curl -fsSL https://ollama.com/install.sh | sh
```

Verify:

```bash
ollama --version
curl http://127.0.0.1:11434/api/tags
```

If the service is not already running in your environment:

```bash
ollama serve
```

## STEP 05 — Load baseline model

The frozen PoC baseline remains:

```bash
ollama pull qwen2.5-coder:3b
ollama run qwen2.5-coder:3b "Return exactly: MODEL_OK"
```

Expected architecture-level result: model downloads and inference returns a response.

## STEP 06 — Install/configure OpenCode

Current official V2 install option:

```bash
curl -fsSL https://opencode.ai/v2/install | bash
```

This repository includes `opencode.jsonc` with:

```json
{
  "$schema": "https://opencode.ai/config.json",
  "model": "ollama/qwen2.5-coder:3b"
}
```

OpenCode V2 can automatically discover Ollama models on the default local endpoint.
Alternative Ollama-assisted launch:

```bash
ollama launch opencode --model qwen2.5-coder:3b
```

## Gate check

Run from repository root:

```bash
python tools/check_local_ai_env.py
```

Required PASS rows:
- Ollama CLI
- Ollama API
- Baseline model
- OpenCode CLI

Only then mark STEP 04–06 PASS.
