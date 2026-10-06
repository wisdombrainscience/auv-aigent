# Gate — Phase 2 P2.3 Provider Contracts

P2.3 may be marked GREEN only when:

- P2.1 and P2.2 are GREEN.
- P2.3 preflight passes.
- ProviderHealth, ProviderModel/List, GenerateRequest and GenerateResponse are
  immutable and strict.
- LocalAIProvider is typed with those contracts.
- MockProvider uses those same contracts.
- unknown fields and malformed types are rejected.
- output_schema is strict JSON and rejects non-finite numbers.
- generate response is complete/non-streaming at the abstract boundary.
- mock response model must match requested model.
- no Ollama/network transport is introduced.
- no retry loop is introduced.
- raw model content is not promoted to Project State.
- existing full regression passes with zero failures.

Gazebo/SITL acceptance is not required because P2.3 does not alter the UAV
execution plane.
