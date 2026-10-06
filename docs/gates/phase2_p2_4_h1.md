# Gate Addendum — P2.4-H1 Probe Entrypoint

P2.4 can close GREEN only after:

- the H1 preflight/apply passes;
- full regression remains green;
- `python3 tools/check_ollama_health.py` succeeds without requiring `PYTHONPATH`;
- the live result is `OLLAMA_HEALTH=PASS`.

The already observed `PYTHONPATH=. ...` result confirms the local Ollama runtime
is healthy, but H1 is required so the documented operator command works as
shipped.
