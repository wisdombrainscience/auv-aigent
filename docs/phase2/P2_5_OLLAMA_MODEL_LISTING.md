# P2.5 — Ollama Model Listing

Implements one bounded local `GET /api/tags`, maps only model names into `ProviderModelList`, treats an empty installed-model list as valid, raises `OllamaModelListError` for listing failures, preserves no-retry/loopback/redirect protections, and leaves generation gated to P2.6.

Live acceptance: `python3 tools/check_ollama_models.py` must print `OLLAMA_MODELS=PASS count=N`.
