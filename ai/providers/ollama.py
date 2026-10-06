from __future__ import annotations

import ipaddress
import json
import socket
from typing import Final
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener

from ai.providers.base import LocalAIProvider
from ai.providers.contracts import (
    GenerateRequest,
    GenerateResponse,
    ProviderContractError,
    ProviderHealth,
    ProviderModelList,
)
from ai.providers.errors import (
    ProviderFailureCode,
    ProviderRuntimeError,
    build_provider_failure,
)


OLLAMA_PROVIDER_CONTRACT_VERSION: Final[str] = "1.2"
DEFAULT_OLLAMA_ENDPOINT: Final[str] = "http://127.0.0.1:11434"
DEFAULT_OLLAMA_HEALTH_TIMEOUT_S: Final[float] = 2.0
MAX_OLLAMA_HEALTH_RESPONSE_BYTES: Final[int] = 64 * 1024
MAX_OLLAMA_MODEL_LIST_RESPONSE_BYTES: Final[int] = 1024 * 1024
DEFAULT_OLLAMA_GENERATE_TIMEOUT_S: Final[float] = 60.0
MAX_OLLAMA_GENERATE_REQUEST_BYTES: Final[int] = 8 * 1024 * 1024
MAX_OLLAMA_GENERATE_RESPONSE_BYTES: Final[int] = 8 * 1024 * 1024


class OllamaProviderConfigurationError(ValueError):
    """Raised when Ollama provider configuration violates local-only policy."""


class OllamaModelListError(ProviderRuntimeError):
    """Structured local Ollama model-list failure."""


class OllamaGenerateError(ProviderRuntimeError):
    """Structured local Ollama generation failure."""


class _NoRedirect(HTTPRedirectHandler):
    """Never follow redirects outside the already-validated loopback endpoint."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None



def _is_timeout_exception(exc: BaseException) -> bool:
    if isinstance(exc, (TimeoutError, socket.timeout)):
        return True
    if isinstance(exc, URLError):
        reason = exc.reason
        return isinstance(reason, (TimeoutError, socket.timeout))
    return False


def _provider_failure(
    *,
    operation: str,
    code: ProviderFailureCode,
    message: str,
    details: dict[str, object] | None = None,
):
    return build_provider_failure(
        provider="ollama",
        operation=operation,
        code=code,
        message=message,
        details=details,
    )


def _model_list_error(
    reason: str,
    code: ProviderFailureCode,
    *,
    details: dict[str, object] | None = None,
) -> OllamaModelListError:
    return OllamaModelListError(
        reason,
        _provider_failure(
            operation="list_models",
            code=code,
            message="Local Ollama model listing failed.",
            details=details,
        ),
    )


def _generate_error(
    reason: str,
    code: ProviderFailureCode,
    *,
    details: dict[str, object] | None = None,
) -> OllamaGenerateError:
    return OllamaGenerateError(
        reason,
        _provider_failure(
            operation="generate",
            code=code,
            message="Local Ollama generation failed.",
            details=details,
        ),
    )


def _normalize_loopback_endpoint(raw: str) -> str:
    if not isinstance(raw, str):
        raise OllamaProviderConfigurationError("endpoint must be a string")
    if not raw or raw != raw.strip():
        raise OllamaProviderConfigurationError(
            "endpoint must be a non-empty canonical URL"
        )

    parsed = urlparse(raw)
    if parsed.scheme != "http":
        raise OllamaProviderConfigurationError(
            "Ollama endpoint must use http on loopback"
        )
    if parsed.username is not None or parsed.password is not None:
        raise OllamaProviderConfigurationError(
            "Ollama endpoint must not contain credentials"
        )
    if parsed.query or parsed.fragment:
        raise OllamaProviderConfigurationError(
            "Ollama endpoint must not contain query or fragment"
        )
    if parsed.path not in ("", "/"):
        raise OllamaProviderConfigurationError(
            "Ollama endpoint must be a base URL without a path"
        )
    if parsed.hostname is None:
        raise OllamaProviderConfigurationError(
            "Ollama endpoint must contain a host"
        )

    try:
        address = ipaddress.ip_address(parsed.hostname)
    except ValueError as exc:
        raise OllamaProviderConfigurationError(
            "Ollama endpoint host must be a numeric loopback address"
        ) from exc

    if not address.is_loopback:
        raise OllamaProviderConfigurationError(
            "Ollama endpoint must use a loopback address"
        )

    try:
        port = parsed.port
    except ValueError as exc:
        raise OllamaProviderConfigurationError(
            "Ollama endpoint port is invalid"
        ) from exc

    if port is None:
        raise OllamaProviderConfigurationError(
            "Ollama endpoint must include an explicit port"
        )
    if not 1 <= port <= 65535:
        raise OllamaProviderConfigurationError(
            "Ollama endpoint port is out of range"
        )

    host = f"[{parsed.hostname}]" if address.version == 6 else parsed.hostname
    return f"http://{host}:{port}"


class OllamaProvider:
    """Local-only Ollama provider with P2.4 health support.

    P2.4 implements only `health()`. The P2.5/P2.6 operations are present so
    the class satisfies the P2.3 provider protocol, but they fail closed with
    NotImplementedError until their dedicated gates are implemented.

    There is exactly one bounded health request and no retry loop.
    """

    def __init__(
        self,
        *,
        endpoint: str = DEFAULT_OLLAMA_ENDPOINT,
        health_timeout_s: float = DEFAULT_OLLAMA_HEALTH_TIMEOUT_S,
        generate_timeout_s: float = DEFAULT_OLLAMA_GENERATE_TIMEOUT_S,
    ) -> None:
        self._endpoint = _normalize_loopback_endpoint(endpoint)

        if isinstance(health_timeout_s, bool) or not isinstance(
            health_timeout_s, (int, float)
        ):
            raise OllamaProviderConfigurationError(
                "health_timeout_s must be a positive finite number"
            )
        timeout = float(health_timeout_s)
        if not 0.05 <= timeout <= 30.0:
            raise OllamaProviderConfigurationError(
                "health_timeout_s must be between 0.05 and 30 seconds"
            )
        self._health_timeout_s = timeout

        if isinstance(generate_timeout_s, bool) or not isinstance(
            generate_timeout_s, (int, float)
        ):
            raise OllamaProviderConfigurationError(
                "generate_timeout_s must be a positive finite number"
            )
        generate_timeout = float(generate_timeout_s)
        if not 0.1 <= generate_timeout <= 300.0:
            raise OllamaProviderConfigurationError(
                "generate_timeout_s must be between 0.1 and 300 seconds"
            )
        self._generate_timeout_s = generate_timeout

        self._opener = build_opener(_NoRedirect())

    @property
    def endpoint(self) -> str:
        return self._endpoint

    @property
    def health_timeout_s(self) -> float:
        return self._health_timeout_s

    @property
    def generate_timeout_s(self) -> float:
        return self._generate_timeout_s

    def health(self) -> ProviderHealth:
        request = Request(
            self._endpoint + "/api/version",
            method="GET",
            headers={
                "Accept": "application/json",
                "User-Agent": "remote-uav-local-ai/1",
            },
        )

        try:
            with self._opener.open(
                request,
                timeout=self._health_timeout_s,
            ) as response:
                status = getattr(response, "status", None)
                if status != 200:
                    return ProviderHealth(
                        ok=False,
                        provider="ollama",
                        detail=f"http_status={status}",
                    )

                body = response.read(
                    MAX_OLLAMA_HEALTH_RESPONSE_BYTES + 1
                )
                if len(body) > MAX_OLLAMA_HEALTH_RESPONSE_BYTES:
                    return ProviderHealth(
                        ok=False,
                        provider="ollama",
                        detail="response_too_large",
                    )

        except HTTPError as exc:
            return ProviderHealth(
                ok=False,
                provider="ollama",
                detail=f"http_status={exc.code}",
            )
        except (TimeoutError, socket.timeout):
            return ProviderHealth(
                ok=False,
                provider="ollama",
                detail="timeout",
            )
        except URLError as exc:
            return ProviderHealth(
                ok=False,
                provider="ollama",
                detail="timeout" if _is_timeout_exception(exc) else "unavailable",
            )
        except OSError as exc:
            return ProviderHealth(
                ok=False,
                provider="ollama",
                detail="timeout" if _is_timeout_exception(exc) else "unavailable",
            )

        try:
            payload = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return ProviderHealth(
                ok=False,
                provider="ollama",
                detail="invalid_response",
            )

        if not isinstance(payload, dict):
            return ProviderHealth(
                ok=False,
                provider="ollama",
                detail="invalid_response",
            )

        version = payload.get("version")
        if (
            not isinstance(version, str)
            or not version
            or version != version.strip()
        ):
            return ProviderHealth(
                ok=False,
                provider="ollama",
                detail="invalid_response",
            )

        return ProviderHealth(
            ok=True,
            provider="ollama",
            detail=f"version={version}",
        )

    def list_models(self) -> ProviderModelList:
        """Return locally installed Ollama model identities.

        P2.7 maps operational failures into the frozen PROVIDER failure category.
        There is still exactly one request and no retry.
        """
        request = Request(
            self._endpoint + "/api/tags",
            method="GET",
            headers={
                "Accept": "application/json",
                "User-Agent": "remote-uav-local-ai/1",
            },
        )
        try:
            with self._opener.open(
                request,
                timeout=self._health_timeout_s,
            ) as response:
                status = getattr(response, "status", None)
                if status != 200:
                    raise _model_list_error(
                        f"http_status={status}",
                        ProviderFailureCode.HTTP_ERROR,
                        details={"http_status": status},
                    )
                body = response.read(
                    MAX_OLLAMA_MODEL_LIST_RESPONSE_BYTES + 1
                )
                if len(body) > MAX_OLLAMA_MODEL_LIST_RESPONSE_BYTES:
                    raise _model_list_error(
                        "response_too_large",
                        ProviderFailureCode.RESPONSE_TOO_LARGE,
                        details={
                            "max_response_bytes": MAX_OLLAMA_MODEL_LIST_RESPONSE_BYTES,
                        },
                    )
        except HTTPError as exc:
            raise _model_list_error(
                f"http_status={exc.code}",
                ProviderFailureCode.HTTP_ERROR,
                details={"http_status": exc.code},
            ) from exc
        except (TimeoutError, socket.timeout) as exc:
            raise _model_list_error(
                "timeout",
                ProviderFailureCode.TIMEOUT,
                details={"timeout_s": self._health_timeout_s},
            ) from exc
        except URLError as exc:
            if _is_timeout_exception(exc):
                raise _model_list_error(
                    "timeout",
                    ProviderFailureCode.TIMEOUT,
                    details={"timeout_s": self._health_timeout_s},
                ) from exc
            raise _model_list_error(
                "unavailable",
                ProviderFailureCode.UNAVAILABLE,
            ) from exc
        except OSError as exc:
            if _is_timeout_exception(exc):
                raise _model_list_error(
                    "timeout",
                    ProviderFailureCode.TIMEOUT,
                    details={"timeout_s": self._health_timeout_s},
                ) from exc
            raise _model_list_error(
                "unavailable",
                ProviderFailureCode.UNAVAILABLE,
            ) from exc

        try:
            payload = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise _model_list_error(
                "invalid_response",
                ProviderFailureCode.INVALID_RESPONSE,
            ) from exc

        if not isinstance(payload, dict):
            raise _model_list_error(
                "invalid_response",
                ProviderFailureCode.INVALID_RESPONSE,
            )
        raw_models = payload.get("models")
        if not isinstance(raw_models, list):
            raise _model_list_error(
                "invalid_response",
                ProviderFailureCode.INVALID_RESPONSE,
            )

        neutral = []
        for index, item in enumerate(raw_models):
            if not isinstance(item, dict):
                raise _model_list_error(
                    f"invalid_model_entry index={index}",
                    ProviderFailureCode.INVALID_RESPONSE,
                    details={"index": index},
                )
            name = item.get("name")
            if name is None:
                name = item.get("model")
            if (
                not isinstance(name, str)
                or not name
                or name != name.strip()
            ):
                raise _model_list_error(
                    f"invalid_model_name index={index}",
                    ProviderFailureCode.INVALID_RESPONSE,
                    details={"index": index},
                )
            neutral.append({"name": name})

        try:
            return ProviderModelList.from_dict({"models": neutral})
        except ProviderContractError as exc:
            raise _model_list_error(
                "invalid_model_list",
                ProviderFailureCode.INVALID_RESPONSE,
            ) from exc

    def generate(
        self,
        request: GenerateRequest,
    ) -> GenerateResponse:
        """Perform one complete local Ollama generation request.

        P2.7 adds structured timeout/operational failure mapping only. It does not
        parse raw model content into AgentResult and does not retry.
        """
        if not isinstance(request, GenerateRequest):
            raise TypeError("request must be GenerateRequest")

        request_payload: dict[str, object] = {
            "model": request.model,
            "prompt": request.prompt,
            "stream": False,
        }
        if request.system_prompt is not None:
            request_payload["system"] = request.system_prompt
        if request.output_schema is not None:
            request_payload["format"] = request.to_dict()["output_schema"]

        body = json.dumps(
            request_payload,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        ).encode("utf-8")
        if len(body) > MAX_OLLAMA_GENERATE_REQUEST_BYTES:
            raise _generate_error(
                "request_too_large",
                ProviderFailureCode.REQUEST_TOO_LARGE,
                details={
                    "request_bytes": len(body),
                    "max_request_bytes": MAX_OLLAMA_GENERATE_REQUEST_BYTES,
                },
            )

        http_request = Request(
            self._endpoint + "/api/generate",
            data=body,
            method="POST",
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
                "User-Agent": "remote-uav-local-ai/1",
            },
        )

        try:
            with self._opener.open(
                http_request,
                timeout=self._generate_timeout_s,
            ) as response:
                status = getattr(response, "status", None)
                if status != 200:
                    raise _generate_error(
                        f"http_status={status}",
                        ProviderFailureCode.HTTP_ERROR,
                        details={"http_status": status},
                    )
                response_body = response.read(
                    MAX_OLLAMA_GENERATE_RESPONSE_BYTES + 1
                )
                if len(response_body) > MAX_OLLAMA_GENERATE_RESPONSE_BYTES:
                    raise _generate_error(
                        "response_too_large",
                        ProviderFailureCode.RESPONSE_TOO_LARGE,
                        details={
                            "max_response_bytes": MAX_OLLAMA_GENERATE_RESPONSE_BYTES,
                        },
                    )
        except HTTPError as exc:
            raise _generate_error(
                f"http_status={exc.code}",
                ProviderFailureCode.HTTP_ERROR,
                details={"http_status": exc.code},
            ) from exc
        except (TimeoutError, socket.timeout) as exc:
            raise _generate_error(
                "timeout",
                ProviderFailureCode.TIMEOUT,
                details={"timeout_s": self._generate_timeout_s},
            ) from exc
        except URLError as exc:
            if _is_timeout_exception(exc):
                raise _generate_error(
                    "timeout",
                    ProviderFailureCode.TIMEOUT,
                    details={"timeout_s": self._generate_timeout_s},
                ) from exc
            raise _generate_error(
                "unavailable",
                ProviderFailureCode.UNAVAILABLE,
            ) from exc
        except OSError as exc:
            if _is_timeout_exception(exc):
                raise _generate_error(
                    "timeout",
                    ProviderFailureCode.TIMEOUT,
                    details={"timeout_s": self._generate_timeout_s},
                ) from exc
            raise _generate_error(
                "unavailable",
                ProviderFailureCode.UNAVAILABLE,
            ) from exc

        try:
            payload = json.loads(response_body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise _generate_error(
                "invalid_response",
                ProviderFailureCode.INVALID_RESPONSE,
            ) from exc

        if not isinstance(payload, dict):
            raise _generate_error(
                "invalid_response",
                ProviderFailureCode.INVALID_RESPONSE,
            )

        response_model = payload.get("model")
        content = payload.get("response")
        done = payload.get("done")
        if response_model != request.model:
            raise _generate_error(
                "model_mismatch",
                ProviderFailureCode.MODEL_MISMATCH,
                details={
                    "requested_model": request.model,
                    "response_model": response_model,
                },
            )
        if done is not True:
            raise _generate_error(
                "incomplete_response",
                ProviderFailureCode.INCOMPLETE_RESPONSE,
            )

        try:
            return GenerateResponse.from_dict(
                {
                    "model": response_model,
                    "content": content,
                    "done": done,
                }
            )
        except ProviderContractError as exc:
            raise _generate_error(
                "invalid_response",
                ProviderFailureCode.INVALID_RESPONSE,
            ) from exc

assert isinstance(OllamaProvider(), LocalAIProvider)
