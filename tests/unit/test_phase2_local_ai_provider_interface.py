from __future__ import annotations

import ast
from pathlib import Path

import pytest

import ai.providers as providers
from ai.providers.base import (
    LOCAL_AI_PROVIDER_INTERFACE_VERSION,
    LocalAIProvider,
)
from ai.providers.contracts import (
    GenerateRequest,
    GenerateResponse,
    ProviderHealth,
    ProviderModelList,
)


class CompleteLocalProvider:
    def health(self) -> ProviderHealth:
        return ProviderHealth(ok=True, provider="test", detail=None)

    def list_models(self) -> ProviderModelList:
        return ProviderModelList(models=())

    def generate(self, request: GenerateRequest) -> GenerateResponse:
        return GenerateResponse(
            model=request.model,
            content="{}",
            done=True,
        )


class MissingGenerate:
    def health(self) -> ProviderHealth:
        return ProviderHealth(ok=True, provider="test", detail=None)

    def list_models(self) -> ProviderModelList:
        return ProviderModelList(models=())


def test_interface_version_is_explicit() -> None:
    assert LOCAL_AI_PROVIDER_INTERFACE_VERSION == "1.1"


def test_package_exports_interface() -> None:
    assert providers.LocalAIProvider is LocalAIProvider
    assert "LocalAIProvider" in providers.__all__
    assert "LOCAL_AI_PROVIDER_INTERFACE_VERSION" in providers.__all__


def test_interface_is_runtime_structural() -> None:
    assert isinstance(CompleteLocalProvider(), LocalAIProvider)


def test_missing_required_method_does_not_match_interface() -> None:
    assert not isinstance(MissingGenerate(), LocalAIProvider)


@pytest.mark.parametrize(
    "method_name",
    ["health", "list_models", "generate"],
)
def test_canonical_provider_operations_exist(method_name: str) -> None:
    assert hasattr(LocalAIProvider, method_name)


def test_required_operation_set_matches_frozen_architecture() -> None:
    public_callables = {
        name
        for name, value in vars(LocalAIProvider).items()
        if not name.startswith("_") and callable(value)
    }
    assert public_callables == {
        "health",
        "list_models",
        "generate",
    }


def test_p2_3_binds_interface_to_provider_contracts() -> None:
    assert (
        str(LocalAIProvider.health.__annotations__["return"])
        in {
            "ProviderHealth",
            "<class 'ai.providers.contracts.ProviderHealth'>",
        }
    )
    assert (
        str(LocalAIProvider.list_models.__annotations__["return"])
        in {
            "ProviderModelList",
            "<class 'ai.providers.contracts.ProviderModelList'>",
        }
    )
    annotations = LocalAIProvider.generate.__annotations__
    assert str(annotations["request"]) in {
        "GenerateRequest",
        "<class 'ai.providers.contracts.GenerateRequest'>",
    }
    assert str(annotations["return"]) in {
        "GenerateResponse",
        "<class 'ai.providers.contracts.GenerateResponse'>",
    }


def test_interface_does_not_own_forbidden_control_plane_responsibilities() -> None:
    forbidden = {
        "project_state",
        "task_state",
        "schedule",
        "scheduler",
        "approve",
        "approve_artifact",
        "agent_role",
        "retry",
        "retry_forever",
        "load_model",
        "unload_model",
    }
    for name in forbidden:
        assert not hasattr(LocalAIProvider, name)


def test_base_module_has_no_network_or_provider_runtime_imports() -> None:
    path = Path(__import__("ai.providers.base", fromlist=["x"]).__file__).resolve()
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


def test_base_module_has_no_uav_execution_plane_imports() -> None:
    path = Path(__import__("ai.providers.base", fromlist=["x"]).__file__).resolve()
    text = path.read_text(encoding="utf-8").lower()
    for fragment in (
        "mavsdk",
        "px4",
        "gazebo",
        "mission_experiment",
    ):
        assert fragment not in text


def test_base_module_contains_no_retry_or_sleep_loop() -> None:
    path = Path(__import__("ai.providers.base", fromlist=["x"]).__file__).resolve()
    text = path.read_text(encoding="utf-8")
    assert "while True" not in text
    assert "time.sleep" not in text
    assert "asyncio.sleep" not in text
    assert "retry_forever" not in text


def test_interface_has_no_cloud_provider_names_or_endpoints() -> None:
    path = Path(__import__("ai.providers.base", fromlist=["x"]).__file__).resolve()
    text = path.read_text(encoding="utf-8").lower()
    for forbidden in (
        "openai",
        "anthropic",
        "gemini",
        "api.openai.com",
        "api.anthropic.com",
    ):
        assert forbidden not in text
