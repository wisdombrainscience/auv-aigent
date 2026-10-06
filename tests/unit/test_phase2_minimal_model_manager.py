from __future__ import annotations

import threading

import pytest

from ai.model_manager import (
    DEFAULT_MAX_PENDING_INFERENCES,
    MAX_LOADED_MODELS,
    MAX_PENDING_INFERENCES,
    MODEL_MANAGER_CONTRACT_VERSION,
    LocalModelProfile,
    ModelKeepAlivePolicy,
    ModelManager,
    ModelManagerBusy,
    ModelManagerModelUnavailable,
    ModelManagerNoModelSelected,
    ModelManagerQueueEmpty,
    ModelManagerQueueFull,
    ModelManagerResourceBlocked,
    ResourceMode,
    ResourceSnapshot,
)
from ai.providers.contracts import (
    GenerateRequest,
    GenerateResponse,
    ProviderHealth,
    ProviderModel,
    ProviderModelList,
)
from ai.providers.errors import (
    ProviderFailureCode,
    ProviderRuntimeError,
    build_provider_failure,
)
from ai.providers.mock import MockProvider
from ai.providers.retry import RetryingLocalAIProvider


def request(model: str = "m1", prompt: str = "test") -> GenerateRequest:
    return GenerateRequest(
        model=model,
        prompt=prompt,
        system_prompt=None,
        output_schema=None,
    )


def response(model: str = "m1", content: str = "{}") -> GenerateResponse:
    return GenerateResponse(
        model=model,
        content=content,
        done=True,
    )


def mock_provider(*responses: GenerateResponse) -> MockProvider:
    return MockProvider(
        health_response=ProviderHealth(
            ok=True,
            provider="mock",
            detail=None,
        ),
        models_response=ProviderModelList(
            models=(
                ProviderModel(name="m1"),
                ProviderModel(name="m2"),
            )
        ),
        generate_responses=responses,
    )


def selected_manager(*responses: GenerateResponse) -> ModelManager:
    manager = ModelManager(mock_provider(*responses))
    manager.select_model("m1")
    return manager


def test_contract_version_and_hard_limits_are_explicit() -> None:
    assert MODEL_MANAGER_CONTRACT_VERSION == "1.0"
    assert MAX_LOADED_MODELS == 1
    assert DEFAULT_MAX_PENDING_INFERENCES == 1
    assert MAX_PENDING_INFERENCES == 1


def test_resource_modes_match_frozen_architecture() -> None:
    assert {item.value for item in ResourceMode} == {
        "AI_HEAVY",
        "SIMULATION_HEAVY",
        "BALANCED",
    }


@pytest.mark.parametrize("name", ["", " m1", "m1 ", 1, None])
def test_model_profile_name_is_strict(name: object) -> None:
    with pytest.raises((ValueError, TypeError)):
        LocalModelProfile(name=name)  # type: ignore[arg-type]


def test_default_snapshot_is_balanced_and_idle() -> None:
    manager = ModelManager(mock_provider())
    snapshot = manager.snapshot()
    assert snapshot.selected_model is None
    assert snapshot.provider_health is None
    assert snapshot.pending_inferences == 0
    assert snapshot.max_pending_inferences == 1
    assert snapshot.inference_active is False
    assert snapshot.resource_mode is ResourceMode.BALANCED
    assert snapshot.simulation_startup_critical is False
    assert snapshot.max_loaded_models == 1


@pytest.mark.parametrize("value", [0, 2, 3, -1, True, 1.5, "1", None])
def test_queue_capacity_cannot_exceed_frozen_bound(value: object) -> None:
    with pytest.raises(ValueError):
        ModelManager(
            mock_provider(),
            max_pending_inferences=value,  # type: ignore[arg-type]
        )


def test_refresh_provider_health_owns_last_snapshot() -> None:
    manager = ModelManager(mock_provider())
    health = manager.refresh_provider_health()
    assert health.ok is True
    assert manager.last_provider_health is health
    assert manager.snapshot().provider_health is health


def test_select_model_requires_local_availability() -> None:
    manager = ModelManager(mock_provider())
    profile = manager.select_model("m1")
    assert profile.name == "m1"
    assert manager.selected_profile == profile

    with pytest.raises(ModelManagerModelUnavailable):
        manager.select_model("missing")


def test_only_one_selected_profile_is_retained() -> None:
    manager = ModelManager(mock_provider())
    manager.select_model("m1")
    manager.select_model("m2")
    snapshot = manager.snapshot()
    assert snapshot.selected_model == "m2"
    assert snapshot.max_loaded_models == 1


def test_generate_requires_selected_model() -> None:
    manager = ModelManager(mock_provider(response()))
    with pytest.raises(ModelManagerNoModelSelected):
        manager.generate(request())


def test_generate_request_must_match_selected_model() -> None:
    manager = selected_manager(response())
    with pytest.raises(ModelManagerModelUnavailable):
        manager.generate(request(model="m2"))


def test_generate_uses_selected_local_provider_once() -> None:
    provider = mock_provider(response(content="ok"))
    manager = ModelManager(provider)
    manager.select_model("m1")

    result = manager.generate(request())

    assert result.content == "ok"
    assert provider.generate_call_count == 1
    assert manager.snapshot().inference_active is False
    assert manager.snapshot().pending_inferences == 0


def test_submit_queue_is_hard_bounded() -> None:
    manager = selected_manager(response(), response())
    manager.submit(request(prompt="first"))
    assert manager.snapshot().pending_inferences == 1

    with pytest.raises(ModelManagerQueueFull):
        manager.submit(request(prompt="second"))

    assert manager.snapshot().pending_inferences == 1


def test_run_next_consumes_exactly_one_queued_request() -> None:
    manager = selected_manager(response(content="first"))
    manager.submit(request(prompt="first"))

    result = manager.run_next()

    assert result.content == "first"
    assert manager.snapshot().pending_inferences == 0
    with pytest.raises(ModelManagerQueueEmpty):
        manager.run_next()


def test_provider_failure_is_not_requeued_automatically() -> None:
    class FailingProvider:
        def health(self):
            return ProviderHealth(ok=True, provider="test", detail=None)
        def list_models(self):
            return ProviderModelList(models=(ProviderModel(name="m1"),))
        def generate(self, req):
            raise ProviderRuntimeError(
                "unavailable",
                build_provider_failure(
                    provider="test",
                    operation="generate",
                    code=ProviderFailureCode.UNAVAILABLE,
                    message="Provider unavailable.",
                ),
            )

    manager = ModelManager(FailingProvider())
    manager.select_model("m1")
    manager.submit(request())

    with pytest.raises(ProviderRuntimeError):
        manager.run_next()

    assert manager.snapshot().pending_inferences == 0
    assert manager.snapshot().inference_active is False


def test_p2_9_retry_wrapper_integrates_without_model_manager_retry_logic() -> None:
    first = ProviderRuntimeError(
        "unavailable",
        build_provider_failure(
            provider="test",
            operation="generate",
            code=ProviderFailureCode.UNAVAILABLE,
            message="Provider unavailable.",
        ),
    )

    class ScriptedProvider:
        def __init__(self):
            self.calls = 0
        def health(self):
            return ProviderHealth(ok=True, provider="test", detail=None)
        def list_models(self):
            return ProviderModelList(models=(ProviderModel(name="m1"),))
        def generate(self, req):
            self.calls += 1
            if self.calls == 1:
                raise first
            return response()

    inner = ScriptedProvider()
    manager = ModelManager(RetryingLocalAIProvider(inner))
    manager.select_model("m1")

    result = manager.generate(request())

    assert result.done is True
    assert inner.calls == 2


def test_simulation_critical_blocks_new_submit_and_generate() -> None:
    manager = selected_manager(response())
    manager.update_resource_state(
        ResourceSnapshot(
            mode=ResourceMode.SIMULATION_HEAVY,
            simulation_startup_critical=True,
        )
    )

    with pytest.raises(ModelManagerResourceBlocked):
        manager.submit(request())
    with pytest.raises(ModelManagerResourceBlocked):
        manager.generate(request())
    assert manager.snapshot().pending_inferences == 0


def test_queued_request_waits_when_resource_becomes_critical() -> None:
    manager = selected_manager(response())
    manager.submit(request())
    manager.update_resource_state(
        ResourceSnapshot(
            mode=ResourceMode.BALANCED,
            simulation_startup_critical=True,
        )
    )

    with pytest.raises(ModelManagerResourceBlocked):
        manager.run_next()
    assert manager.snapshot().pending_inferences == 1

    manager.update_resource_state(ResourceSnapshot())
    assert manager.run_next().done is True
    assert manager.snapshot().pending_inferences == 0


def test_model_selection_cannot_change_while_request_is_queued() -> None:
    manager = selected_manager(response())
    manager.submit(request())
    with pytest.raises(ModelManagerBusy):
        manager.select_model("m2")


class LifecycleProvider:
    def __init__(self):
        self.loaded: list[str] = []
        self.unloaded: list[str] = []
    def health(self):
        return ProviderHealth(ok=True, provider="lifecycle", detail=None)
    def list_models(self):
        return ProviderModelList(models=(ProviderModel(name="m1"),))
    def generate(self, req):
        return response(model=req.model)
    def load_model(self, model: str) -> None:
        self.loaded.append(model)
    def unload_model(self, model: str) -> None:
        self.unloaded.append(model)


def test_optional_model_lifecycle_is_used_only_when_supported() -> None:
    provider = LifecycleProvider()
    manager = ModelManager(provider)
    manager.select_model("m1")

    assert manager.lifecycle_supported is True
    assert manager.ensure_selected_model_loaded() is True
    assert provider.loaded == ["m1"]
    assert manager.release_selected_model() is True
    assert provider.unloaded == ["m1"]


def test_lifecycle_methods_fail_closed_when_provider_does_not_support_them() -> None:
    manager = selected_manager()
    assert manager.lifecycle_supported is False
    assert manager.ensure_selected_model_loaded() is False
    assert manager.release_selected_model() is False


def test_release_on_simulation_critical_policy() -> None:
    provider = LifecycleProvider()
    manager = ModelManager(provider)
    manager.select_model(
        "m1",
        keep_alive_policy=(
            ModelKeepAlivePolicy.RELEASE_ON_SIMULATION_CRITICAL
        ),
    )
    manager.update_resource_state(
        ResourceSnapshot(
            mode=ResourceMode.SIMULATION_HEAVY,
            simulation_startup_critical=True,
        )
    )

    assert manager.apply_lifecycle_policy() is True
    assert provider.unloaded == ["m1"]


def test_provider_default_keep_alive_policy_does_not_force_unload() -> None:
    provider = LifecycleProvider()
    manager = ModelManager(provider)
    manager.select_model("m1")
    manager.update_resource_state(
        ResourceSnapshot(
            mode=ResourceMode.SIMULATION_HEAVY,
            simulation_startup_critical=True,
        )
    )

    assert manager.apply_lifecycle_policy() is False
    assert provider.unloaded == []


def test_no_model_selected_blocks_lifecycle_actions() -> None:
    manager = ModelManager(mock_provider())
    with pytest.raises(ModelManagerNoModelSelected):
        manager.ensure_selected_model_loaded()
    with pytest.raises(ModelManagerNoModelSelected):
        manager.release_selected_model()
    with pytest.raises(ModelManagerNoModelSelected):
        manager.apply_lifecycle_policy()


def test_model_manager_source_has_no_unbounded_queue_worker_or_retry_loop() -> None:
    import ai.model_manager as module

    text = open(module.__file__, encoding="utf-8").read()
    assert "while True" not in text
    assert "time.sleep" not in text
    assert "asyncio.sleep" not in text
    assert "ThreadPoolExecutor" not in text
    assert "multiprocessing" not in text
    assert "retry_forever" not in text


def test_model_manager_does_not_own_future_layers_or_uav_execution() -> None:
    import ai.model_manager as module

    text = open(module.__file__, encoding="utf-8").read().lower()
    for fragment in (
        "project_state",
        "task_state",
        "agent_role",
        "approve_artifact",
        "mavsdk",
        "px4",
        "gazebo",
        "ollamaprovider",
    ):
        assert fragment not in text
