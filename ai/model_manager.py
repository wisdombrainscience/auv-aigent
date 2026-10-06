from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import threading
from typing import Protocol, runtime_checkable

from ai.providers.base import LocalAIProvider
from ai.providers.contracts import (
    GenerateRequest,
    GenerateResponse,
    ProviderHealth,
    ProviderModelList,
)


MODEL_MANAGER_CONTRACT_VERSION = "1.0"
MAX_LOADED_MODELS = 1
DEFAULT_MAX_PENDING_INFERENCES = 1
MAX_PENDING_INFERENCES = 1


class ResourceMode(str, Enum):
    AI_HEAVY = "AI_HEAVY"
    SIMULATION_HEAVY = "SIMULATION_HEAVY"
    BALANCED = "BALANCED"


class ModelKeepAlivePolicy(str, Enum):
    PROVIDER_DEFAULT = "PROVIDER_DEFAULT"
    RELEASE_ON_SIMULATION_CRITICAL = "RELEASE_ON_SIMULATION_CRITICAL"


@dataclass(frozen=True)
class LocalModelProfile:
    name: str
    keep_alive_policy: ModelKeepAlivePolicy = ModelKeepAlivePolicy.PROVIDER_DEFAULT

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name or self.name != self.name.strip():
            raise ValueError("model profile name must be clean non-empty text")
        if not isinstance(self.keep_alive_policy, ModelKeepAlivePolicy):
            raise TypeError("keep_alive_policy must be ModelKeepAlivePolicy")


@dataclass(frozen=True)
class ResourceSnapshot:
    mode: ResourceMode = ResourceMode.BALANCED
    simulation_startup_critical: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.mode, ResourceMode):
            raise TypeError("mode must be ResourceMode")
        if type(self.simulation_startup_critical) is not bool:
            raise TypeError("simulation_startup_critical must be bool")


@dataclass(frozen=True)
class ModelManagerSnapshot:
    selected_model: str | None
    keep_alive_policy: ModelKeepAlivePolicy | None
    provider_health: ProviderHealth | None
    pending_inferences: int
    max_pending_inferences: int
    inference_active: bool
    resource_mode: ResourceMode
    simulation_startup_critical: bool
    lifecycle_supported: bool
    max_loaded_models: int


@runtime_checkable
class ModelLifecycleProvider(Protocol):
    """Optional provider capability used only when a runtime supports it."""

    def load_model(self, model: str) -> None:
        ...

    def unload_model(self, model: str) -> None:
        ...


class ModelManagerError(RuntimeError):
    pass


class ModelManagerNoModelSelected(ModelManagerError):
    pass


class ModelManagerModelUnavailable(ModelManagerError):
    pass


class ModelManagerQueueFull(ModelManagerError):
    pass


class ModelManagerQueueEmpty(ModelManagerError):
    pass


class ModelManagerBusy(ModelManagerError):
    pass


class ModelManagerResourceBlocked(ModelManagerError):
    pass


class ModelManager:
    """Minimal synchronous Model Manager for the local-provider foundation.

    Responsibilities implemented by P2.10:

    - one selected local model profile;
    - provider-health snapshot ownership;
    - a hard-bounded pending inference queue;
    - keep-alive/release policy ownership;
    - basic resource-mode awareness;
    - optional model load/unload actions when a provider supports them.

    This class intentionally owns no project state, task state, agent roles,
    artifact approval, scheduling, retry policy, model-output parsing, or UAV
    execution. A P2.9 RetryingLocalAIProvider may be supplied as the provider
    when bounded provider retry is desired.
    """

    def __init__(
        self,
        provider: LocalAIProvider,
        *,
        max_pending_inferences: int = DEFAULT_MAX_PENDING_INFERENCES,
        resource_snapshot: ResourceSnapshot | None = None,
    ) -> None:
        if not isinstance(provider, LocalAIProvider):
            raise TypeError("provider must satisfy LocalAIProvider")
        if isinstance(max_pending_inferences, bool) or not isinstance(
            max_pending_inferences, int
        ):
            raise ValueError("max_pending_inferences must be an integer")
        if not 1 <= max_pending_inferences <= MAX_PENDING_INFERENCES:
            raise ValueError(
                "max_pending_inferences must be exactly within the hard bound 1"
            )
        if resource_snapshot is None:
            resource_snapshot = ResourceSnapshot()
        if not isinstance(resource_snapshot, ResourceSnapshot):
            raise TypeError("resource_snapshot must be ResourceSnapshot")

        self._provider = provider
        self._max_pending_inferences = max_pending_inferences
        self._resource = resource_snapshot
        self._selected: LocalModelProfile | None = None
        self._last_health: ProviderHealth | None = None
        self._pending: list[GenerateRequest] = []
        self._inference_active = False
        self._lock = threading.Lock()

    @property
    def selected_profile(self) -> LocalModelProfile | None:
        with self._lock:
            return self._selected

    @property
    def last_provider_health(self) -> ProviderHealth | None:
        with self._lock:
            return self._last_health

    @property
    def lifecycle_supported(self) -> bool:
        return isinstance(self._provider, ModelLifecycleProvider)

    def snapshot(self) -> ModelManagerSnapshot:
        with self._lock:
            selected = self._selected
            return ModelManagerSnapshot(
                selected_model=None if selected is None else selected.name,
                keep_alive_policy=(
                    None if selected is None else selected.keep_alive_policy
                ),
                provider_health=self._last_health,
                pending_inferences=len(self._pending),
                max_pending_inferences=self._max_pending_inferences,
                inference_active=self._inference_active,
                resource_mode=self._resource.mode,
                simulation_startup_critical=(
                    self._resource.simulation_startup_critical
                ),
                lifecycle_supported=self.lifecycle_supported,
                max_loaded_models=MAX_LOADED_MODELS,
            )

    def refresh_provider_health(self) -> ProviderHealth:
        health = self._provider.health()
        if not isinstance(health, ProviderHealth):
            raise TypeError("provider health() must return ProviderHealth")
        with self._lock:
            self._last_health = health
        return health

    def list_models(self) -> ProviderModelList:
        models = self._provider.list_models()
        if not isinstance(models, ProviderModelList):
            raise TypeError("provider list_models() must return ProviderModelList")
        return models

    def select_model(
        self,
        name: str,
        *,
        keep_alive_policy: ModelKeepAlivePolicy = ModelKeepAlivePolicy.PROVIDER_DEFAULT,
    ) -> LocalModelProfile:
        profile = LocalModelProfile(
            name=name,
            keep_alive_policy=keep_alive_policy,
        )
        models = self.list_models()
        available = {model.name for model in models.models}
        if profile.name not in available:
            raise ModelManagerModelUnavailable(
                f"selected model is not locally available: {profile.name}"
            )

        with self._lock:
            if self._inference_active or self._pending:
                raise ModelManagerBusy(
                    "cannot change selected model while inference is active or queued"
                )
            self._selected = profile
        return profile

    def update_resource_state(self, snapshot: ResourceSnapshot) -> None:
        if not isinstance(snapshot, ResourceSnapshot):
            raise TypeError("snapshot must be ResourceSnapshot")
        with self._lock:
            self._resource = snapshot

    def ensure_selected_model_loaded(self) -> bool:
        with self._lock:
            selected = self._selected
            if selected is None:
                raise ModelManagerNoModelSelected(
                    "no local model profile is selected"
                )
            if self._inference_active:
                raise ModelManagerBusy(
                    "cannot request a lifecycle load during active inference"
                )
            model_name = selected.name

        if not isinstance(self._provider, ModelLifecycleProvider):
            return False
        self._provider.load_model(model_name)
        return True

    def release_selected_model(self) -> bool:
        with self._lock:
            selected = self._selected
            if selected is None:
                raise ModelManagerNoModelSelected(
                    "no local model profile is selected"
                )
            if self._inference_active or self._pending:
                raise ModelManagerBusy(
                    "cannot unload a model while inference is active or queued"
                )
            model_name = selected.name

        if not isinstance(self._provider, ModelLifecycleProvider):
            return False
        self._provider.unload_model(model_name)
        return True

    def apply_lifecycle_policy(self) -> bool:
        """Apply only the minimal release policy currently frozen by P2.10.

        A provider-default profile causes no action. A release-on-critical
        profile may be unloaded only when simulation startup is marked critical
        and the provider explicitly supports lifecycle operations.
        """

        with self._lock:
            selected = self._selected
            if selected is None:
                raise ModelManagerNoModelSelected(
                    "no local model profile is selected"
                )
            should_release = (
                selected.keep_alive_policy
                is ModelKeepAlivePolicy.RELEASE_ON_SIMULATION_CRITICAL
                and self._resource.simulation_startup_critical
            )

        if not should_release:
            return False
        return self.release_selected_model()

    def _validate_request_locked(self, request: GenerateRequest) -> None:
        if not isinstance(request, GenerateRequest):
            raise TypeError("request must be GenerateRequest")
        if self._selected is None:
            raise ModelManagerNoModelSelected(
                "no local model profile is selected"
            )
        if request.model != self._selected.name:
            raise ModelManagerModelUnavailable(
                "generate request model does not match selected model profile"
            )

    def _check_resource_gate_locked(self) -> None:
        if self._resource.simulation_startup_critical:
            raise ModelManagerResourceBlocked(
                "new AI inference is blocked while simulation startup is resource-critical"
            )

    def submit(self, request: GenerateRequest) -> None:
        """Place one request into the hard-bounded manager inference queue."""

        with self._lock:
            self._validate_request_locked(request)
            self._check_resource_gate_locked()
            if len(self._pending) >= self._max_pending_inferences:
                raise ModelManagerQueueFull(
                    "model-manager inference queue is full"
                )
            self._pending.append(request)

    def run_next(self) -> GenerateResponse:
        """Run the oldest queued request, without requeueing on failure."""

        with self._lock:
            self._check_resource_gate_locked()
            if self._inference_active:
                raise ModelManagerBusy(
                    "one local inference is already active"
                )
            if not self._pending:
                raise ModelManagerQueueEmpty(
                    "model-manager inference queue is empty"
                )
            request = self._pending.pop(0)
            self._inference_active = True

        try:
            response = self._provider.generate(request)
            if not isinstance(response, GenerateResponse):
                raise TypeError(
                    "provider generate() must return GenerateResponse"
                )
            return response
        finally:
            with self._lock:
                self._inference_active = False

    def generate(self, request: GenerateRequest) -> GenerateResponse:
        """Single-call path for the default one-agent/one-model deployment.

        It fails fast if another inference or pending queued request exists.
        It never waits, sleeps, spawns a worker, or creates an unbounded queue.
        """

        with self._lock:
            self._validate_request_locked(request)
            self._check_resource_gate_locked()
            if self._inference_active or self._pending:
                raise ModelManagerBusy(
                    "model manager is busy; use the bounded queue explicitly"
                )
            self._pending.append(request)
            queued = self._pending.pop(0)
            self._inference_active = True

        try:
            response = self._provider.generate(queued)
            if not isinstance(response, GenerateResponse):
                raise TypeError(
                    "provider generate() must return GenerateResponse"
                )
            return response
        finally:
            with self._lock:
                self._inference_active = False
