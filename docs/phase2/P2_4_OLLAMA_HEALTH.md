# P2.4 — Ollama Health

Status: candidate for target gate.

## Scope

P2.4 introduces the first real local Ollama runtime contact.

`OllamaProvider.health()` performs exactly one bounded:

```text
GET http://127.0.0.1:11434/api/version
```

and maps a valid response such as:

```json
{"version": "0.12.3"}
```

to:

```json
{
  "ok": true,
  "provider": "ollama",
  "detail": "version=0.12.3"
}
```

No generation request is made.

## Local-only enforcement

The endpoint must:

- use `http`;
- use a numeric loopback address;
- include an explicit port;
- contain no credentials;
- contain no path, query, or fragment.

The default is exactly:

```text
http://127.0.0.1:11434
```

Redirect following is disabled so a local endpoint cannot redirect the provider
to a remote host.

## Bounded behavior

Health has:

- one network attempt;
- no retry;
- configurable timeout constrained to 0.05–30 seconds;
- default timeout 2 seconds;
- maximum health body 64 KiB.

Network/unavailable state produces `ProviderHealth(ok=false, ...)`; it does not
start a retry loop.

Formal provider failure mapping/timeout taxonomy remains assigned to the later
Phase-2 error-handling packages.

## Deliberate gates

P2.4 intentionally leaves:

```text
list_models() -> NotImplementedError until P2.5
generate()    -> NotImplementedError until P2.6
```

Therefore passing P2.4 proves local Ollama reachability only. It does not prove
that a model is installed or that generation works.

## Live target acceptance

After apply + full regression, run:

```bash
python3 tools/check_ollama_health.py
```

P2.4 runtime acceptance requires:

```text
OLLAMA_HEALTH=PASS
```

If it reports `OLLAMA_HEALTH=UNAVAILABLE`, stop there. Do not retry blindly;
first determine whether Ollama is installed/running and listening on the frozen
loopback endpoint.
