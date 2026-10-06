from __future__ import annotations

import ast
from pathlib import Path

import pytest

import ai.providers as providers
from ai.providers.base import LocalAIProvider
from ai.providers.contracts import (
    GenerateRequest,
    GenerateResponse,
    ProviderHealth,
    ProviderModel,
    ProviderModelList,
)
from ai.providers.mock import (
    MOCK_PROVIDER_CONTRACT_VERSION,
    MockProvider,
    MockProviderScriptExhausted,
)


def request(model: str = "mock-model") -> GenerateRequest:
    return GenerateRequest(
        model=model,
        prompt="test",
        system_prompt=None,
        output_schema=None,
    )


def response(
    content: str,
    model: str = "mock-model",
) -> GenerateResponse:
    return GenerateResponse(
        model=model,
        content=content,
        done=True,
    )


def test_contract_version_is_explicit() -> None:
    assert MOCK_PROVIDER_CONTRACT_VERSION == "1.1"


def test_package_exports_mock_provider() -> None:
    assert providers.MockProvider is MockProvider
    assert "MockProvider" in providers.__all__
    assert "MockProviderScriptExhausted" in providers.__all__


def test_mock_provider_satisfies_local_provider_protocol() -> None:
    assert isinstance(MockProvider(), LocalAIProvider)


def test_health_is_deterministic() -> None:
    health = ProviderHealth(
        ok=True,
        provider="mock",
        detail=None,
    )
    provider = MockProvider(health_response=health)
    assert provider.health() is health
    assert provider.health() is health


def test_list_models_is_deterministic() -> None:
    models = ProviderModelList(
        models=(
            ProviderModel(name="model-a"),
            ProviderModel(name="model-b"),
        )
    )
    provider = MockProvider(models_response=models)
    assert provider.list_models() is models
    assert provider.list_models() is models


def test_generate_consumes_script_in_order() -> None:
    first = response("first")
    second = response("second")
    provider = MockProvider(
        generate_responses=[first, second]
    )

    assert provider.generate(request()) is first
    assert provider.generate(request()) is second
    assert provider.generate_call_count == 2
    assert provider.remaining_generate_responses == 0


def test_generate_script_exhaustion_is_explicit() -> None:
    provider = MockProvider(
        generate_responses=[response("only")]
    )
    assert provider.generate(request()).content == "only"

    with pytest.raises(
        MockProviderScriptExhausted,
        match="script exhausted",
    ):
        provider.generate(request())

    assert provider.generate_call_count == 1
    assert provider.remaining_generate_responses == 0


def test_empty_script_is_immediately_exhausted() -> None:
    provider = MockProvider()
    with pytest.raises(MockProviderScriptExhausted):
        provider.generate(request())


def test_generate_does_not_repeat_last_response() -> None:
    provider = MockProvider(
        generate_responses=[response("one")]
    )
    assert provider.generate(request()).content == "one"
    with pytest.raises(MockProviderScriptExhausted):
        provider.generate(request())


@pytest.mark.parametrize(
    "bad",
    [
        "not-a-response-sequence",
        b"bytes",
        bytearray(b"bytes"),
        iter([1, 2]),
        {1, 2},
        1,
        None,
    ],
)
def test_generate_script_requires_finite_sequence(bad: object) -> None:
    with pytest.raises(TypeError):
        MockProvider(
            generate_responses=bad,  # type: ignore[arg-type]
        )


def test_generate_script_requires_typed_responses() -> None:
    with pytest.raises(TypeError):
        MockProvider(
            generate_responses=[1, 2],  # type: ignore[list-item]
        )


def test_mock_provider_does_not_store_request_history() -> None:
    provider = MockProvider(
        generate_responses=[
            response("one"),
            response("two"),
        ]
    )
    provider.generate(request())
    provider.generate(request())

    assert not hasattr(provider, "requests")
    assert not hasattr(provider, "history")
    assert not hasattr(provider, "call_history")


def test_mock_provider_rejects_untyped_request() -> None:
    provider = MockProvider(
        generate_responses=[response("one")]
    )
    with pytest.raises(TypeError):
        provider.generate({"model": "mock-model"})  # type: ignore[arg-type]


def test_mock_provider_rejects_response_model_mismatch() -> None:
    provider = MockProvider(
        generate_responses=[
            response("x", model="different-model")
        ]
    )
    with pytest.raises(ValueError, match="does not match"):
        provider.generate(request(model="mock-model"))


def test_mock_provider_has_no_network_imports() -> None:
    path = Path(
        __import__("ai.providers.mock", fromlist=["x"]).__file__
    ).resolve()
    tree = ast.parse(path.read_text(encoding="utf-8"))
    forbidden_roots = {
        "requests",
        "httpx",
        "urllib",
        "socket",
        "ollama",
        "aiohttp",
    }

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            names = [node.module or ""]
        else:
            continue

        for name in names:
            assert name.split(".", 1)[0] not in forbidden_roots, name


def test_mock_provider_has_no_retry_or_sleep_loop() -> None:
    path = Path(
        __import__("ai.providers.mock", fromlist=["x"]).__file__
    ).resolve()
    text = path.read_text(encoding="utf-8")
    assert "while True" not in text
    assert "time.sleep" not in text
    assert "asyncio.sleep" not in text
    assert "retry_forever" not in text


def test_mock_provider_has_no_uav_execution_imports() -> None:
    path = Path(
        __import__("ai.providers.mock", fromlist=["x"]).__file__
    ).resolve()
    text = path.read_text(encoding="utf-8").lower()
    for fragment in (
        "mavsdk",
        "px4",
        "gazebo",
        "mission_experiment",
    ):
        assert fragment not in text
