from __future__ import annotations

from contextlib import contextmanager
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading

import pytest

from ai.providers.base import LocalAIProvider
from ai.providers.contracts import GenerateRequest
from ai.providers.ollama import (
    DEFAULT_OLLAMA_ENDPOINT,
    DEFAULT_OLLAMA_HEALTH_TIMEOUT_S,
    OLLAMA_PROVIDER_CONTRACT_VERSION,
    OllamaProvider,
    OllamaProviderConfigurationError,
)


class _Handler(BaseHTTPRequestHandler):
    status = 200
    payload: bytes = b'{"version":"0.0.test"}'
    hits = 0
    redirect_location: str | None = None

    def do_GET(self):
        type(self).hits += 1

        if self.path != "/api/version":
            self.send_response(404)
            self.end_headers()
            return

        if type(self).redirect_location is not None:
            self.send_response(302)
            self.send_header(
                "Location",
                type(self).redirect_location,
            )
            self.end_headers()
            return

        self.send_response(type(self).status)
        self.send_header("Content-Type", "application/json")
        self.send_header(
            "Content-Length",
            str(len(type(self).payload)),
        )
        self.end_headers()
        self.wfile.write(type(self).payload)

    def log_message(self, format, *args):
        return


@contextmanager
def local_server(
    *,
    status: int = 200,
    payload: bytes = b'{"version":"0.0.test"}',
    redirect_location: str | None = None,
):
    class Handler(_Handler):
        pass

    Handler.status = status
    Handler.payload = payload
    Handler.hits = 0
    Handler.redirect_location = redirect_location

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(
        target=server.serve_forever,
        daemon=True,
    )
    thread.start()
    try:
        endpoint = (
            f"http://127.0.0.1:{server.server_address[1]}"
        )
        yield endpoint, Handler
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_contract_version_and_defaults_are_explicit() -> None:
    assert OLLAMA_PROVIDER_CONTRACT_VERSION == "1.2"
    assert DEFAULT_OLLAMA_ENDPOINT == "http://127.0.0.1:11434"
    assert DEFAULT_OLLAMA_HEALTH_TIMEOUT_S == 2.0


def test_ollama_provider_satisfies_provider_protocol() -> None:
    assert isinstance(OllamaProvider(), LocalAIProvider)


def test_successful_health_probe_maps_version() -> None:
    with local_server(
        payload=b'{"version":"0.12.3"}'
    ) as (endpoint, handler):
        provider = OllamaProvider(endpoint=endpoint)
        health = provider.health()

    assert handler.hits == 1
    assert health.ok is True
    assert health.provider == "ollama"
    assert health.detail == "version=0.12.3"


def test_health_uses_exact_version_path_once() -> None:
    with local_server() as (endpoint, handler):
        provider = OllamaProvider(endpoint=endpoint)
        provider.health()

    assert handler.hits == 1


def test_http_error_returns_unhealthy_without_retry() -> None:
    with local_server(
        status=503,
        payload=b'{"error":"busy"}',
    ) as (endpoint, handler):
        health = OllamaProvider(endpoint=endpoint).health()

    assert handler.hits == 1
    assert health.ok is False
    assert health.detail == "http_status=503"


@pytest.mark.parametrize(
    "payload",
    [
        b"",
        b"not-json",
        b"[]",
        b"{}",
        b'{"version":1}',
        b'{"version":""}',
        b'{"version":" bad"}',
    ],
)
def test_invalid_version_response_is_unhealthy(
    payload: bytes,
) -> None:
    with local_server(
        payload=payload
    ) as (endpoint, handler):
        health = OllamaProvider(endpoint=endpoint).health()

    assert handler.hits == 1
    assert health.ok is False
    assert health.detail == "invalid_response"


def test_oversized_health_body_is_rejected() -> None:
    oversized = json.dumps(
        {"version": "x" * (64 * 1024)}
    ).encode("utf-8")
    with local_server(
        payload=oversized
    ) as (endpoint, handler):
        health = OllamaProvider(endpoint=endpoint).health()

    assert handler.hits == 1
    assert health.ok is False
    assert health.detail == "response_too_large"


def test_unavailable_loopback_port_returns_unhealthy() -> None:
    # Bind an ephemeral port, close it, then probe once. No external network.
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    port = server.server_address[1]
    server.server_close()

    provider = OllamaProvider(
        endpoint=f"http://127.0.0.1:{port}",
        health_timeout_s=0.1,
    )
    health = provider.health()

    assert health.ok is False
    assert health.detail == "unavailable"


def test_redirect_is_not_followed() -> None:
    with local_server(
        redirect_location="http://192.0.2.1:11434/api/version"
    ) as (endpoint, handler):
        health = OllamaProvider(endpoint=endpoint).health()

    assert handler.hits == 1
    assert health.ok is False
    assert health.detail == "http_status=302"


@pytest.mark.parametrize(
    "endpoint",
    [
        "https://127.0.0.1:11434",
        "http://localhost:11434",
        "http://192.168.1.2:11434",
        "http://8.8.8.8:11434",
        "http://127.0.0.1",
        "http://user:pass@127.0.0.1:11434",
        "http://127.0.0.1:11434/api",
        "http://127.0.0.1:11434?x=1",
        "",
        " http://127.0.0.1:11434",
    ],
)
def test_noncanonical_or_nonloopback_endpoint_is_rejected(
    endpoint: str,
) -> None:
    with pytest.raises(OllamaProviderConfigurationError):
        OllamaProvider(endpoint=endpoint)


def test_ipv6_loopback_endpoint_is_accepted() -> None:
    provider = OllamaProvider(endpoint="http://[::1]:11434")
    assert provider.endpoint == "http://[::1]:11434"


@pytest.mark.parametrize(
    "timeout",
    [
        0,
        -1,
        0.01,
        31,
        True,
        "2",
        None,
    ],
)
def test_health_timeout_is_bounded_and_validated(
    timeout: object,
) -> None:
    with pytest.raises(OllamaProviderConfigurationError):
        OllamaProvider(
            health_timeout_s=timeout,  # type: ignore[arg-type]
        )


def test_health_timeout_accepts_safe_range() -> None:
    provider = OllamaProvider(health_timeout_s=0.05)
    assert provider.health_timeout_s == 0.05

    provider = OllamaProvider(health_timeout_s=30)
    assert provider.health_timeout_s == 30.0


def test_model_listing_gate_is_released_by_p2_5() -> None:
    assert callable(OllamaProvider.list_models)


def test_generation_gate_is_released_by_p2_6() -> None:
    assert callable(OllamaProvider.generate)

