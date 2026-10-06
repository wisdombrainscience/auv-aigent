from __future__ import annotations

import json
import math

import pytest

from ai.providers.base import (
    LOCAL_AI_PROVIDER_INTERFACE_VERSION,
    LocalAIProvider,
)
from ai.providers.contracts import (
    PROVIDER_WIRE_CONTRACT_VERSION,
    GenerateRequest,
    GenerateResponse,
    ProviderContractError,
    ProviderHealth,
    ProviderModel,
    ProviderModelList,
)
from ai.providers.mock import (
    MOCK_PROVIDER_CONTRACT_VERSION,
    MockProvider,
    MockProviderScriptExhausted,
)


def test_contract_versions_are_explicit() -> None:
    assert PROVIDER_WIRE_CONTRACT_VERSION == "1.0"
    assert LOCAL_AI_PROVIDER_INTERFACE_VERSION == "1.1"
    assert MOCK_PROVIDER_CONTRACT_VERSION == "1.1"


def test_health_round_trip() -> None:
    payload = {
        "ok": True,
        "provider": "ollama",
        "detail": None,
    }
    health = ProviderHealth.from_dict(payload)
    assert health.to_dict() == payload
    assert json.loads(json.dumps(health.to_dict())) == payload


@pytest.mark.parametrize("bad", [1, 0, "true", None])
def test_health_ok_requires_real_boolean(bad: object) -> None:
    with pytest.raises(ProviderContractError):
        ProviderHealth.from_dict(
            {
                "ok": bad,
                "provider": "ollama",
                "detail": None,
            }
        )


@pytest.mark.parametrize("extra", [{"status": "UP"}, {"url": "x"}])
def test_health_rejects_unknown_fields(extra: dict[str, object]) -> None:
    payload: dict[str, object] = {
        "ok": True,
        "provider": "ollama",
        "detail": None,
    }
    payload.update(extra)
    with pytest.raises(ProviderContractError, match="extra="):
        ProviderHealth.from_dict(payload)


def test_model_list_round_trip() -> None:
    payload = {
        "models": [
            {"name": "qwen-local:7b"},
            {"name": "coder-local:7b"},
        ]
    }
    result = ProviderModelList.from_dict(payload)
    assert result.to_dict() == payload


def test_empty_model_list_is_valid() -> None:
    result = ProviderModelList.from_dict({"models": []})
    assert result.models == ()


def test_duplicate_model_names_are_rejected() -> None:
    with pytest.raises(ProviderContractError, match="duplicate model"):
        ProviderModelList.from_dict(
            {
                "models": [
                    {"name": "m1"},
                    {"name": "m1"},
                ]
            }
        )


@pytest.mark.parametrize("bad", [None, {}, "models", 1])
def test_models_must_be_array(bad: object) -> None:
    with pytest.raises(ProviderContractError):
        ProviderModelList.from_dict({"models": bad})


@pytest.mark.parametrize(
    "payload",
    [
        {"name": ""},
        {"name": " model"},
        {"name": "model "},
        {"name": 1},
        {"name": "m", "size": 1},
    ],
)
def test_model_identity_is_strict(payload: dict[str, object]) -> None:
    with pytest.raises(ProviderContractError):
        ProviderModel.from_dict(payload)


def generate_request_payload() -> dict[str, object]:
    return {
        "model": "local-model:7b",
        "prompt": "Return a structured engineering proposal.",
        "system_prompt": "You are the requirements engineer.",
        "output_schema": {
            "type": "object",
            "required": ["status"],
            "properties": {
                "status": {
                    "type": "string",
                    "const": "PROPOSED",
                }
            },
        },
    }


def test_generate_request_round_trip_and_schema_snapshot() -> None:
    payload = generate_request_payload()
    result = GenerateRequest.from_dict(payload)
    assert result.to_dict() == payload

    schema = payload["output_schema"]
    assert isinstance(schema, dict)
    schema["type"] = "array"

    assert result.to_dict()["output_schema"]["type"] == "object"


@pytest.mark.parametrize(
    "field",
    ["model", "prompt", "system_prompt", "output_schema"],
)
def test_generate_request_requires_exact_fields(field: str) -> None:
    payload = generate_request_payload()
    del payload[field]
    with pytest.raises(ProviderContractError, match="missing="):
        GenerateRequest.from_dict(payload)


def test_generate_request_rejects_unknown_fields() -> None:
    payload = generate_request_payload()
    payload["temperature"] = 0.2
    with pytest.raises(ProviderContractError, match="extra=temperature"):
        GenerateRequest.from_dict(payload)


@pytest.mark.parametrize(
    ("field", "bad"),
    [
        ("model", ""),
        ("model", " model"),
        ("prompt", ""),
        ("prompt", " prompt"),
        ("system_prompt", ""),
        ("system_prompt", " prompt"),
    ],
)
def test_generate_request_rejects_malformed_text(
    field: str,
    bad: object,
) -> None:
    payload = generate_request_payload()
    payload[field] = bad
    with pytest.raises(ProviderContractError):
        GenerateRequest.from_dict(payload)


def test_generate_request_allows_null_optional_fields() -> None:
    payload = generate_request_payload()
    payload["system_prompt"] = None
    payload["output_schema"] = None

    request = GenerateRequest.from_dict(payload)
    assert request.system_prompt is None
    assert request.output_schema is None


@pytest.mark.parametrize("bad", [[], "schema", 1, True])
def test_output_schema_must_be_object_or_null(bad: object) -> None:
    payload = generate_request_payload()
    payload["output_schema"] = bad
    with pytest.raises(ProviderContractError):
        GenerateRequest.from_dict(payload)


@pytest.mark.parametrize("bad_number", [math.nan, math.inf, -math.inf])
def test_output_schema_rejects_nonfinite_numbers(
    bad_number: float,
) -> None:
    payload = generate_request_payload()
    payload["output_schema"] = {"x": bad_number}
    with pytest.raises(ProviderContractError, match="non-finite"):
        GenerateRequest.from_dict(payload)


def test_output_schema_rejects_non_json_values() -> None:
    payload = generate_request_payload()
    payload["output_schema"] = {"bad": {1, 2}}
    with pytest.raises(ProviderContractError):
        GenerateRequest.from_dict(payload)


def generate_response_payload() -> dict[str, object]:
    return {
        "model": "local-model:7b",
        "content": '{"status":"PROPOSED"}',
        "done": True,
    }


def test_generate_response_round_trip() -> None:
    payload = generate_response_payload()
    result = GenerateResponse.from_dict(payload)
    assert result.to_dict() == payload


@pytest.mark.parametrize("bad", [False, 0, 1, "true", None])
def test_generate_response_requires_complete_done_true(
    bad: object,
) -> None:
    payload = generate_response_payload()
    payload["done"] = bad
    with pytest.raises(ProviderContractError):
        GenerateResponse.from_dict(payload)


@pytest.mark.parametrize(
    ("field", "bad"),
    [
        ("model", ""),
        ("content", ""),
        ("content", " "),
        ("content", 1),
    ],
)
def test_generate_response_rejects_malformed_text(
    field: str,
    bad: object,
) -> None:
    payload = generate_response_payload()
    payload[field] = bad
    with pytest.raises(ProviderContractError):
        GenerateResponse.from_dict(payload)


def test_mock_provider_uses_typed_contracts() -> None:
    health = ProviderHealth(
        ok=True,
        provider="mock",
        detail=None,
    )
    models = ProviderModelList(
        models=(ProviderModel(name="local-model:7b"),)
    )
    response = GenerateResponse(
        model="local-model:7b",
        content='{"status":"PROPOSED"}',
        done=True,
    )

    provider = MockProvider(
        health_response=health,
        models_response=models,
        generate_responses=[response],
    )

    assert isinstance(provider, LocalAIProvider)
    assert provider.health() is health
    assert provider.list_models() is models

    request = GenerateRequest.from_dict(
        generate_request_payload()
    )
    assert provider.generate(request) is response


def test_mock_provider_rejects_untyped_generate_request() -> None:
    provider = MockProvider(
        generate_responses=[
            GenerateResponse(
                model="m1",
                content="{}",
                done=True,
            )
        ]
    )
    with pytest.raises(TypeError, match="GenerateRequest"):
        provider.generate({"model": "m1"})  # type: ignore[arg-type]


def test_mock_provider_rejects_untyped_configured_responses() -> None:
    with pytest.raises(TypeError):
        MockProvider(
            health_response={"ok": True},  # type: ignore[arg-type]
        )
    with pytest.raises(TypeError):
        MockProvider(
            models_response={"models": []},  # type: ignore[arg-type]
        )
    with pytest.raises(TypeError):
        MockProvider(
            generate_responses=[{"content": "x"}],  # type: ignore[list-item]
        )


def test_mock_response_model_must_match_request_model() -> None:
    provider = MockProvider(
        generate_responses=[
            GenerateResponse(
                model="other-model",
                content="{}",
                done=True,
            )
        ]
    )
    request = GenerateRequest(
        model="requested-model",
        prompt="x",
        system_prompt=None,
        output_schema=None,
    )

    with pytest.raises(ValueError, match="does not match"):
        provider.generate(request)


def test_mock_script_exhaustion_remains_bounded() -> None:
    provider = MockProvider(
        generate_responses=[
            GenerateResponse(
                model="m1",
                content="{}",
                done=True,
            )
        ]
    )
    request = GenerateRequest(
        model="m1",
        prompt="x",
        system_prompt=None,
        output_schema=None,
    )
    provider.generate(request)
    with pytest.raises(MockProviderScriptExhausted):
        provider.generate(request)
