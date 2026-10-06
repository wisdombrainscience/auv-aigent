from __future__ import annotations

from contextlib import contextmanager
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading

import pytest

from ai.providers.contracts import GenerateRequest, GenerateResponse
from ai.providers.ollama import (
    DEFAULT_OLLAMA_GENERATE_TIMEOUT_S,
    MAX_OLLAMA_GENERATE_REQUEST_BYTES,
    MAX_OLLAMA_GENERATE_RESPONSE_BYTES,
    OLLAMA_PROVIDER_CONTRACT_VERSION,
    OllamaGenerateError,
    OllamaProvider,
    OllamaProviderConfigurationError,
)


class GenerateHandlerBase(BaseHTTPRequestHandler):
    status = 200
    payload = b'{"model":"m1","response":"ok","done":true}'
    hits = 0
    paths: list[str] = []
    requests: list[dict[str, object]] = []
    redirect_location: str | None = None

    def do_POST(self):
        type(self).hits += 1
        type(self).paths.append(self.path)
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length)
        try:
            type(self).requests.append(json.loads(raw.decode("utf-8")))
        except Exception:
            type(self).requests.append({"_invalid": True})
        if self.path != "/api/generate":
            self.send_response(404); self.end_headers(); return
        if type(self).redirect_location is not None:
            self.send_response(302)
            self.send_header("Location", type(self).redirect_location)
            self.end_headers(); return
        self.send_response(type(self).status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(type(self).payload)))
        self.end_headers()
        self.wfile.write(type(self).payload)

    def log_message(self, format, *args):
        return


@contextmanager
def generate_server(*, status=200, payload=None, redirect_location=None):
    class Handler(GenerateHandlerBase):
        pass
    Handler.status = status
    Handler.payload = payload if payload is not None else GenerateHandlerBase.payload
    Handler.hits = 0
    Handler.paths = []
    Handler.requests = []
    Handler.redirect_location = redirect_location
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}", Handler
    finally:
        server.shutdown(); server.server_close(); thread.join(timeout=2)


def req(*, model="m1", schema=None, system=None):
    return GenerateRequest(
        model=model,
        prompt="Return a short local response.",
        system_prompt=system,
        output_schema=schema,
    )


def test_version_and_bounds_are_explicit():
    assert OLLAMA_PROVIDER_CONTRACT_VERSION == "1.2"
    assert DEFAULT_OLLAMA_GENERATE_TIMEOUT_S == 60.0
    assert MAX_OLLAMA_GENERATE_REQUEST_BYTES == 8 * 1024 * 1024
    assert MAX_OLLAMA_GENERATE_RESPONSE_BYTES == 8 * 1024 * 1024


def test_plain_generation_posts_once_and_maps_response():
    with generate_server() as (endpoint, h):
        result = OllamaProvider(endpoint=endpoint).generate(req())
    assert isinstance(result, GenerateResponse)
    assert result.to_dict() == {"model":"m1","content":"ok","done":True}
    assert h.hits == 1
    assert h.paths == ["/api/generate"]
    assert h.requests == [{
        "model":"m1",
        "prompt":"Return a short local response.",
        "stream":False,
    }]


def test_system_prompt_is_forwarded_only_when_present():
    with generate_server() as (endpoint, h):
        OllamaProvider(endpoint=endpoint).generate(req(system="System role."))
    assert h.requests[0]["system"] == "System role."


def test_output_schema_maps_to_ollama_format_object():
    schema={"type":"object","properties":{"status":{"type":"string"}},"required":["status"]}
    payload=json.dumps({"model":"m1","response":"{\\\"status\\\":\\\"ok\\\"}","done":True}).encode()
    with generate_server(payload=payload) as (endpoint, h):
        OllamaProvider(endpoint=endpoint).generate(req(schema=schema))
    assert h.requests[0]["format"] == schema
    assert h.requests[0]["stream"] is False


def test_provider_does_not_add_sampling_keepalive_or_thinking_policy():
    with generate_server() as (endpoint, h):
        OllamaProvider(endpoint=endpoint).generate(req())
    body=h.requests[0]
    for forbidden in ("options","keep_alive","think","temperature"):
        assert forbidden not in body


def test_response_provider_metadata_is_not_promoted():
    payload=json.dumps({
        "model":"m1","response":"ok","done":True,
        "created_at":"x","total_duration":123,"eval_count":4
    }).encode()
    with generate_server(payload=payload) as (endpoint, _):
        result=OllamaProvider(endpoint=endpoint).generate(req())
    assert result.to_dict()=={"model":"m1","content":"ok","done":True}


@pytest.mark.parametrize("payload",[
    b"", b"not-json", b"[]", b"{}",
    b'{"model":"m1","response":"ok","done":false}',
    b'{"model":"other","response":"ok","done":true}',
    b'{"model":"m1","response":"","done":true}',
    b'{"model":"m1","response":1,"done":true}',
])
def test_invalid_or_incomplete_response_fails_closed(payload):
    with generate_server(payload=payload) as (endpoint, h):
        with pytest.raises(OllamaGenerateError):
            OllamaProvider(endpoint=endpoint).generate(req())
    assert h.hits == 1


def test_http_failure_is_explicit_and_not_retried():
    with generate_server(status=503) as (endpoint, h):
        with pytest.raises(OllamaGenerateError, match="http_status=503") as caught:
            OllamaProvider(endpoint=endpoint).generate(req())
    assert h.hits == 1
    assert caught.value.failure.to_dict()["code"] == "PROVIDER_HTTP_ERROR"
    assert caught.value.failure.to_dict()["details"]["http_status"] == 503


def test_redirect_is_not_followed():
    with generate_server(redirect_location="http://192.0.2.1:11434/api/generate") as (endpoint,h):
        with pytest.raises(OllamaGenerateError, match="http_status=302"):
            OllamaProvider(endpoint=endpoint).generate(req())
    assert h.hits == 1


def test_unavailable_endpoint_is_explicit():
    server=ThreadingHTTPServer(("127.0.0.1",0),GenerateHandlerBase)
    port=server.server_address[1]; server.server_close()
    with pytest.raises(OllamaGenerateError, match="unavailable"):
        OllamaProvider(
            endpoint=f"http://127.0.0.1:{port}",
            generate_timeout_s=0.1,
        ).generate(req())


@pytest.mark.parametrize("timeout",[0,-1,0.01,301,True,"60",None])
def test_generate_timeout_is_bounded(timeout):
    with pytest.raises(OllamaProviderConfigurationError):
        OllamaProvider(generate_timeout_s=timeout)


def test_generate_timeout_safe_range():
    assert OllamaProvider(generate_timeout_s=0.1).generate_timeout_s == 0.1
    assert OllamaProvider(generate_timeout_s=300).generate_timeout_s == 300.0


def test_generate_requires_typed_request():
    with pytest.raises(TypeError, match="GenerateRequest"):
        OllamaProvider().generate({"model":"m1"})


def test_generate_source_contains_no_retry_loop():
    import ai.providers.ollama as module
    text=open(module.__file__,encoding="utf-8").read()
    assert "while True" not in text
    assert "time.sleep" not in text
    assert "asyncio.sleep" not in text
