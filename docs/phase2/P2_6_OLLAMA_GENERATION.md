# P2.6 — Ollama Generation Transport

P2.6 implements one complete local `POST /api/generate` call. The request forces `stream=false`, forwards provider-neutral `model`, `prompt`, optional `system_prompt`, and maps `output_schema` to Ollama `format`. Sampling, keep-alive, thinking level, model selection, retry, AgentResult parsing, and Project State promotion remain out of scope.

The returned Ollama `response` remains raw `GenerateResponse.content`. P2.8 owns structured model-output parsing/invalid-output handling.

Generation is bounded by a dedicated transport timeout (default 60 s; hard range 0.1–300 s), an 8 MiB request cap, and an 8 MiB response cap. P2.7 will refine timeout/error mapping into the failure taxonomy. There is no retry in P2.6.

Live acceptance requires an explicit installed model so P2.6 does not introduce Model Manager policy:

```bash
python3 tools/check_ollama_generate.py --model qwen3:4b-instruct-8k
```

Required prefix: `OLLAMA_GENERATE=PASS`.
