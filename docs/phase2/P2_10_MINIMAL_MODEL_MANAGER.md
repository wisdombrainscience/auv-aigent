# P2.10 — Minimal Model Manager

Status: candidate for target gate and CP2 closure.

## Scope

P2.10 implements the minimal Model Manager responsibilities frozen in the V8
architecture baseline:

```text
selected local model profile
provider health
inference queue
model keep-alive policy
basic resource awareness
model load/unload policy when supported
```

No multi-agent behavior is introduced.

## One-model default

The manager exposes:

```text
MAX_LOADED_MODELS = 1
```

and retains exactly one selected `LocalModelProfile`.

Selection is validated against `LocalAIProvider.list_models()` before the
profile becomes active. A `GenerateRequest` must name the selected model; the
manager never silently substitutes another model.

## Bounded inference queue

The pending inference queue has:

```text
default capacity = 1
hard maximum    = 1
```

`submit()` queues one request and fails closed on overflow. `run_next()` runs
the oldest pending request. `generate()` is the single-call path for the
default one-agent/one-model deployment and fails fast when the manager is busy.

There is no background worker, unbounded waiter list, sleep loop, or retry loop.
P2.9 provider retry remains a separate provider wrapper underneath the manager.

## Provider health

`refresh_provider_health()` obtains and stores the latest immutable
`ProviderHealth` snapshot. Health is not converted into a UAV/simulation
verdict.

## Resource awareness

The frozen resource modes are represented exactly:

```text
AI_HEAVY
SIMULATION_HEAVY
BALANCED
```

P2.10 implements the simpler baseline rule: if
`simulation_startup_critical=true`, no new AI inference may start. Queued work
remains bounded and can run after the resource-critical condition clears.

## Keep-alive and lifecycle policy

Profiles own one of two minimal policies:

```text
PROVIDER_DEFAULT
RELEASE_ON_SIMULATION_CRITICAL
```

The existing `LocalAIProvider` contract is not widened. Optional load/unload is
used only if a provider structurally exposes `load_model()` and
`unload_model()`. Current OllamaProvider does not expose that optional lifecycle
interface, so live P2.10 leaves Ollama lifecycle under provider/runtime default
behavior.

This preserves the frozen provider API:

```text
health()
list_models()
generate()
```

## Deliberate non-goals

P2.10 adds no:

- Project State or persistence — Phase 3;
- Artifact Registry — Phase 4;
- task scheduler/DAG — Phase 5;
- Agent Engine or agent roles — Phase 6;
- Orchestrator/multi-agent logic;
- RAG;
- cloud model provider;
- direct PX4/MAVSDK/UAV execution path.

## Live target acceptance

After apply and regression, run through the manager using one already-installed
local model:

```bash
python3 tools/check_model_manager.py \
  --model qwen3:4b-instruct-8k
```

Required prefix:

```text
MODEL_MANAGER=PASS
```

The probe uses `RetryingLocalAIProvider(OllamaProvider(...))` under the manager,
so it validates the completed P2.1–P2.10 local-provider stack without adding an
Agent Engine.
