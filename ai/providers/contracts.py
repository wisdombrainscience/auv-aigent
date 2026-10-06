from __future__ import annotations

from dataclasses import dataclass
import math
from types import MappingProxyType
from typing import Any, Mapping, TypeAlias


PROVIDER_WIRE_CONTRACT_VERSION = "1.0"


class ProviderContractError(ValueError):
    """Raised when a provider request/response violates P2.3."""


JSONScalar: TypeAlias = None | bool | int | float | str
JSONValue: TypeAlias = (
    JSONScalar
    | tuple["JSONValue", ...]
    | Mapping[str, "JSONValue"]
)


def _require_clean_text(
    value: Any,
    *,
    field: str,
    allow_empty: bool = False,
) -> str:
    if not isinstance(value, str):
        raise ProviderContractError(f"{field} must be a string")
    if not allow_empty and not value:
        raise ProviderContractError(f"{field} must not be empty")
    if value != value.strip():
        raise ProviderContractError(
            f"{field} must not contain surrounding whitespace"
        )
    return value


def _freeze_json(value: Any, *, path: str) -> JSONValue:
    if value is None or isinstance(value, (bool, str)):
        return value

    if isinstance(value, int):
        return value

    if isinstance(value, float):
        if not math.isfinite(value):
            raise ProviderContractError(
                f"{path} contains a non-finite number"
            )
        return value

    if isinstance(value, (list, tuple)):
        return tuple(
            _freeze_json(item, path=f"{path}[{index}]")
            for index, item in enumerate(value)
        )

    if isinstance(value, Mapping):
        frozen: dict[str, JSONValue] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise ProviderContractError(
                    f"{path} contains a non-string object key"
                )
            if not key:
                raise ProviderContractError(
                    f"{path} contains an empty object key"
                )
            frozen[key] = _freeze_json(item, path=f"{path}.{key}")
        return MappingProxyType(frozen)

    raise ProviderContractError(
        f"{path} contains unsupported JSON value type "
        f"{type(value).__name__}"
    )


def _thaw_json(value: JSONValue) -> Any:
    if isinstance(value, tuple):
        return [_thaw_json(item) for item in value]
    if isinstance(value, Mapping):
        return {key: _thaw_json(item) for key, item in value.items()}
    return value


def _require_exact_keys(
    raw: Mapping[str, Any],
    expected: frozenset[str],
    *,
    object_name: str,
) -> None:
    keys = frozenset(raw.keys())
    if keys == expected:
        return

    missing = sorted(expected - keys)
    extra = sorted(keys - expected)
    details: list[str] = []
    if missing:
        details.append("missing=" + ",".join(missing))
    if extra:
        details.append("extra=" + ",".join(str(item) for item in extra))
    raise ProviderContractError(
        f"{object_name} must contain exactly the P2.3 fields"
        + (" (" + "; ".join(details) + ")" if details else "")
    )


_PROVIDER_HEALTH_KEYS = frozenset({"ok", "provider", "detail"})
_PROVIDER_MODEL_KEYS = frozenset({"name"})
_PROVIDER_MODEL_LIST_KEYS = frozenset({"models"})
_GENERATE_REQUEST_KEYS = frozenset(
    {"model", "prompt", "system_prompt", "output_schema"}
)
_GENERATE_RESPONSE_KEYS = frozenset({"model", "content", "done"})


@dataclass(frozen=True)
class ProviderHealth:
    """Local provider health snapshot.

    `ok` is a transport/provider availability signal only. It is not a UAV
    health verdict and not a model-output quality verdict.
    """

    ok: bool
    provider: str
    detail: str | None

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "ProviderHealth":
        if not isinstance(raw, Mapping):
            raise ProviderContractError("provider health must be an object")
        _require_exact_keys(
            raw,
            _PROVIDER_HEALTH_KEYS,
            object_name="provider health",
        )

        if type(raw["ok"]) is not bool:
            raise ProviderContractError("ok must be a boolean")

        detail = raw["detail"]
        if detail is not None:
            detail = _require_clean_text(detail, field="detail")

        return cls(
            ok=raw["ok"],
            provider=_require_clean_text(
                raw["provider"],
                field="provider",
            ),
            detail=detail,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "provider": self.provider,
            "detail": self.detail,
        }


@dataclass(frozen=True)
class ProviderModel:
    """One locally available model identity."""

    name: str

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "ProviderModel":
        if not isinstance(raw, Mapping):
            raise ProviderContractError("provider model must be an object")
        _require_exact_keys(
            raw,
            _PROVIDER_MODEL_KEYS,
            object_name="provider model",
        )
        return cls(
            name=_require_clean_text(raw["name"], field="name")
        )

    def to_dict(self) -> dict[str, str]:
        return {"name": self.name}


@dataclass(frozen=True)
class ProviderModelList:
    """Immutable model-list response."""

    models: tuple[ProviderModel, ...]

    @classmethod
    def from_dict(
        cls,
        raw: Mapping[str, Any],
    ) -> "ProviderModelList":
        if not isinstance(raw, Mapping):
            raise ProviderContractError(
                "provider model list must be an object"
            )
        _require_exact_keys(
            raw,
            _PROVIDER_MODEL_LIST_KEYS,
            object_name="provider model list",
        )

        models_raw = raw["models"]
        if not isinstance(models_raw, (list, tuple)):
            raise ProviderContractError("models must be an array")

        models = tuple(
            item
            if isinstance(item, ProviderModel)
            else ProviderModel.from_dict(item)
            for item in models_raw
        )

        seen: set[str] = set()
        for model in models:
            if model.name in seen:
                raise ProviderContractError(
                    f"duplicate model name: {model.name}"
                )
            seen.add(model.name)

        return cls(models=models)

    def to_dict(self) -> dict[str, Any]:
        return {
            "models": [
                model.to_dict()
                for model in self.models
            ]
        }


@dataclass(frozen=True)
class GenerateRequest:
    """One complete, non-streaming local generation request.

    P2.3 deliberately freezes only provider-neutral fields:

    - model: local model identity chosen by the future Model Manager;
    - prompt: task/context prompt;
    - system_prompt: optional role/policy prompt;
    - output_schema: optional strict-JSON schema data for structured generation.

    Sampling parameters, keep-alive, queueing, resource policy, and retries are
    not part of this request contract.
    """

    model: str
    prompt: str
    system_prompt: str | None
    output_schema: Mapping[str, JSONValue] | None

    @classmethod
    def from_dict(
        cls,
        raw: Mapping[str, Any],
    ) -> "GenerateRequest":
        if not isinstance(raw, Mapping):
            raise ProviderContractError(
                "generate request must be an object"
            )
        _require_exact_keys(
            raw,
            _GENERATE_REQUEST_KEYS,
            object_name="generate request",
        )

        system_prompt = raw["system_prompt"]
        if system_prompt is not None:
            system_prompt = _require_clean_text(
                system_prompt,
                field="system_prompt",
            )

        output_schema = raw["output_schema"]
        frozen_schema: Mapping[str, JSONValue] | None
        if output_schema is None:
            frozen_schema = None
        else:
            if not isinstance(output_schema, Mapping):
                raise ProviderContractError(
                    "output_schema must be an object or null"
                )
            frozen = _freeze_json(
                output_schema,
                path="output_schema",
            )
            assert isinstance(frozen, Mapping)
            frozen_schema = frozen

        return cls(
            model=_require_clean_text(raw["model"], field="model"),
            prompt=_require_clean_text(raw["prompt"], field="prompt"),
            system_prompt=system_prompt,
            output_schema=frozen_schema,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "model": self.model,
            "prompt": self.prompt,
            "system_prompt": self.system_prompt,
            "output_schema": (
                None
                if self.output_schema is None
                else _thaw_json(self.output_schema)
            ),
        }


@dataclass(frozen=True)
class GenerateResponse:
    """One complete provider-neutral generation response.

    `content` remains raw model text. Parsing that text into AgentResult or
    another structured contract belongs to later model-output handling; the
    provider cannot promote raw model text directly into Project State.
    """

    model: str
    content: str
    done: bool

    @classmethod
    def from_dict(
        cls,
        raw: Mapping[str, Any],
    ) -> "GenerateResponse":
        if not isinstance(raw, Mapping):
            raise ProviderContractError(
                "generate response must be an object"
            )
        _require_exact_keys(
            raw,
            _GENERATE_RESPONSE_KEYS,
            object_name="generate response",
        )

        if type(raw["done"]) is not bool:
            raise ProviderContractError("done must be a boolean")
        if raw["done"] is not True:
            raise ProviderContractError(
                "LocalAIProvider.generate must return one complete response"
            )

        return cls(
            model=_require_clean_text(raw["model"], field="model"),
            content=_require_clean_text(
                raw["content"],
                field="content",
            ),
            done=True,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "model": self.model,
            "content": self.content,
            "done": self.done,
        }
