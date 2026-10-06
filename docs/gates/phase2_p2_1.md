# Gate — Phase 2 P2.1 LocalAIProvider

P2.1 may be marked GREEN only when:

- CP1 is GREEN.
- P2.1 preflight passes.
- `ai/providers` exists as an isolated new module.
- `LocalAIProvider` exposes exactly `health`, `list_models`, and `generate`.
- A complete structural provider satisfies the protocol.
- An incomplete provider does not satisfy it.
- Request/response wire schemas remain deferred to P2.3.
- No network transport or Ollama implementation is introduced.
- No retry/sleep loop is introduced.
- No cloud provider is introduced.
- No Project State/task/scheduler/approval responsibility moves into provider.
- No UAV execution-plane module is imported.
- Existing full regression passes with zero failures.

Gazebo/SITL acceptance is not required because P2.1 does not alter the UAV
execution plane.
