# Gate — Phase 2 P2.6 Ollama Generation

GREEN requires P2.5 GREEN, installer/regression PASS, exactly one bounded local `POST /api/generate`, `stream=false`, explicit model identity, correct optional system/schema mapping, no sampling/keep-alive/model-manager policy, no retry, raw content only, generation not promoted to Project State, and live `check_ollama_generate.py --model <installed>` returning `OLLAMA_GENERATE=PASS`.
