# Gate — Phase 2 P2.7 Provider Timeout Handling

P2.7 is GREEN only when:

- P2.6 is GREEN.
- preflight/apply pass.
- full regression has zero failures.
- direct and wrapped timeouts map to `PROVIDER_TIMEOUT`.
- connection/unavailability maps to `PROVIDER_UNAVAILABLE`.
- HTTP failures map to `PROVIDER_HTTP_ERROR` with status detail.
- provider protocol failures remain distinct from P2.8 model-content failures.
- errors carry immutable C1.8 `FailureDescriptor` values.
- health distinguishes `timeout` from `unavailable`.
- no failure record authorizes retry or state transition.
- each provider operation still performs at most one transport attempt.
- no retry loop is introduced; P2.9 remains gated.
- no cloud or UAV execution-plane change is introduced.

No additional live generation is required for P2.7 because P2.6 already proved
the local Ollama generation path on target; P2.7 is deterministic error-path
hardening.
