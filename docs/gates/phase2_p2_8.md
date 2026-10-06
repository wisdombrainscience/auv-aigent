# Gate — Phase 2 P2.8 Malformed Model Output

P2.8 is GREEN only when:

- P2.7 is GREEN.
- installer preflight/apply pass.
- full target regression has zero failures.
- valid model JSON becomes a C1.5 `AgentResult`.
- malformed JSON fails as `MODEL_INVALID_OUTPUT`.
- duplicate keys and non-finite numbers fail closed.
- non-object top-level JSON fails closed.
- AgentResult contract violations fail as `MODEL_INVALID_OUTPUT`.
- raw model content is not embedded in failure details.
- malformed content cannot mutate Project State or artifact/task state.
- no retry is introduced; P2.9 remains gated.
- no cloud or UAV execution-plane change is introduced.

No additional live Ollama call is required because this is a deterministic
post-generation model-content boundary and P2.6 already accepted live local
generation on target.
