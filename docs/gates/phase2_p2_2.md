# Gate — Phase 2 P2.2 MockProvider

P2.2 may be marked GREEN only when:

- P2.1 is GREEN.
- P2.2 preflight passes.
- MockProvider satisfies LocalAIProvider.
- health/list_models are deterministic.
- generate consumes a finite script in exact order.
- exhausted script raises explicitly and never repeats forever.
- configured inputs are snapshotted.
- mutable returned values are detached from provider state.
- requests are not retained in an unbounded history collection.
- no network/Ollama code is introduced.
- no retry/sleep loop is introduced.
- no UAV execution-plane code is imported.
- existing full regression passes with zero failures.

Gazebo/SITL acceptance is not required because P2.2 does not alter the UAV
execution plane.
