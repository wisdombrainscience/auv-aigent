# Gate — Phase 2 P2.10 Minimal Model Manager

P2.10 is GREEN only when:

- P2.9 is GREEN.
- installer preflight/apply pass.
- full target regression has zero failures.
- exactly one selected local model profile is owned by the manager.
- model selection is validated against the local provider model list.
- provider-health snapshot ownership is tested.
- pending inference queue capacity is hard-bounded to 1.
- queue overflow and empty-run paths fail closed.
- simulation-startup-critical state blocks new AI inference.
- resource modes are exactly AI_HEAVY, SIMULATION_HEAVY, BALANCED.
- keep-alive/release policy is owned by the manager.
- load/unload occurs only through an optional provider lifecycle capability.
- P2.9 provider retry remains separate and bounded.
- no background worker, unbounded queue, sleep/retry loop, Project State,
  Task Engine, Agent Engine, multi-agent, cloud provider, or UAV execution-plane
  change is introduced.
- live target `python3 tools/check_model_manager.py --model <installed-model>`
  returns `MODEL_MANAGER=PASS`.

Passing P2.10 together with prior P2.1–P2.9 gates closes CP2 Local Provider
Stable.
