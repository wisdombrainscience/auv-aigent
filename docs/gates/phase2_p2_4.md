# Gate — Phase 2 P2.4 Ollama Health

P2.4 is GREEN only when:

- P2.3 is GREEN.
- P2.4 installer preflight/apply pass.
- full target regression has zero failures.
- endpoint policy rejects non-loopback/noncanonical endpoints.
- redirect following is disabled.
- health performs one bounded request with no retry.
- oversized and malformed health responses fail closed.
- model listing remains gated to P2.5.
- generation remains gated to P2.6.
- live target `python3 tools/check_ollama_health.py` returns
  `OLLAMA_HEALTH=PASS`.
- no cloud endpoint/provider is introduced.
- no UAV execution-plane code is modified.

If the live probe is unavailable, P2.4 remains not-GREEN until the local Ollama
runtime is installed/configured/running. Do not compensate with cloud fallback.
