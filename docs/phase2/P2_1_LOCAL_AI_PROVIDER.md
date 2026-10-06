# P2.1 — LocalAIProvider Interface

Status: candidate for target gate.

## Scope

P2.1 starts Phase 2 by creating the local-only provider abstraction required by
the frozen architecture.

The interface contains exactly the three provider responsibilities:

```text
health()
list_models()
generate()
```

The implementation is a runtime-checkable structural protocol under:

```text
ai/providers/base.py
```

## Deliberate P2.1 boundary

P2.1 does **not** define provider wire schemas yet.

The input/output annotations are intentionally opaque because the roadmap
assigns request/response contracts to P2.3. This prevents P2.1 from silently
freezing a transport schema before the dedicated work package.

P2.1 also does not implement:

- MockProvider — P2.2;
- request/response contracts — P2.3;
- Ollama health — P2.4;
- model listing — P2.5;
- generation transport — P2.6;
- timeout handling — P2.7;
- malformed output handling — P2.8;
- provider retry — P2.9;
- Model Manager — P2.10.

## Ownership boundary

LocalAIProvider does not own:

- Project State;
- task state;
- scheduling;
- agent roles;
- artifact approval;
- Model Manager policy;
- retry loops;
- PX4/MAVSDK/Gazebo/UAV execution.

No cloud provider is introduced.

## Gate intent

P2.1 is GREEN only after the target full regression remains green. The next
authorized package is P2.2 MockProvider.
