from __future__ import annotations

import asyncio
import inspect

import pytest

import evaluation.direct_runtime as direct


def test_reliability_readiness_defaults_match_frozen_policy():
    sig = inspect.signature(direct.wait_flight_ready)
    assert sig.parameters["timeout_s"].default == 60.0
    assert sig.parameters["stable_s"].default == 2.0
    fresh = inspect.signature(direct.run_fresh_nav_trial)
    assert fresh.parameters["startup_retries"].default == 3
    assert fresh.parameters["startup_wait_s"].default == 60.0
    assert fresh.parameters["startup_stable_s"].default == 2.0


def test_fresh_trial_stops_simulator_after_each_failed_readiness(monkeypatch):
    calls = {"start": 0, "stop": 0, "ready": 0}

    class FakeSim:
        def __init__(self, project):
            pass
        def start_new(self, start):
            calls["start"] += 1
        def stop(self):
            calls["stop"] += 1

    async def never_ready(*, timeout_s, stable_s):
        calls["ready"] += 1
        raise TimeoutError("not ready")

    monkeypatch.setattr(direct, "SimulationSupervisor", FakeSim)
    monkeypatch.setattr(direct, "wait_flight_ready", never_ready)

    with pytest.raises(RuntimeError, match="failed after 2 attempts"):
        asyncio.run(
            direct.run_fresh_nav_trial(
                scenario="TEST",
                target=[1.0, 0.0, -3.0],
                startup_retries=2,
                startup_retry_cooldown_s=0.0,
            )
        )

    assert calls == {"start": 2, "stop": 2, "ready": 2}
