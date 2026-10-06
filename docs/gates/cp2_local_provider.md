# CP2 — Local Provider Stable

CP2 is GREEN only after the target has confirmed:

```text
P2.1  LocalAIProvider                 GREEN
P2.2  MockProvider                    GREEN
P2.3  request/response contracts      GREEN
P2.4  Ollama health                   GREEN
P2.5  model listing                   GREEN
P2.6  generation                      GREEN
P2.7  timeout/error handling          GREEN
P2.8  malformed model output          GREEN
P2.9  bounded provider retry          GREEN
P2.10 minimal Model Manager           GREEN
```

CP2 does not authorize work from Phase 3 onward until P2.10 target regression
and the live Model Manager acceptance probe both pass.

CP2 preserves the trust boundary:

```text
local AI only
no cloud fallback
no direct AI → PX4/MAVSDK/actuators
no Agent Engine yet
no multi-agent yet
no Project State persistence yet
```
