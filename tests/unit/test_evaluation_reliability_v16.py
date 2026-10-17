from __future__ import annotations

import asyncio
import inspect
from pathlib import Path
from types import SimpleNamespace

import evaluation.direct_runtime as direct
import evaluation.run_reliability as rel


class _FakeAdapter:
    def __init__(self, health_seq):
        self.health_seq = list(health_seq)
        self.i = 0
        self.closed = False
    async def connect(self):
        return None
    async def close(self):
        self.closed = True
    async def get_state(self):
        return SimpleNamespace(connected=True, localization_valid=True)
    async def _cached_telemetry(self, name):
        idx = min(self.i, len(self.health_seq) - 1)
        value = self.health_seq[idx]
        self.i += 1
        return value
    async def get_position(self):
        return SimpleNamespace(x=0.0, y=0.0, z=0.0)


def test_wait_flight_ready_keeps_adapter_factory_contract():
    sig = inspect.signature(direct.wait_flight_ready)
    assert "adapter_factory" in sig.parameters
    holder = {}
    def factory():
        a = _FakeAdapter([
            SimpleNamespace(
                is_local_position_ok=True,
                is_global_position_ok=True,
                is_home_position_ok=True,
                is_armable=True,
            )
        ])
        holder["a"] = a
        return a
    result = asyncio.run(direct.wait_flight_ready(
        timeout_s=0.2, poll_s=0.001, stable_s=0.0, adapter_factory=factory
    ))
    assert result["ready"] is True
    assert result["readiness_source"] == "is_armable"
    assert holder["a"].closed is True


def test_direct_condition_retries_pre_execution_infra_until_requested_n(monkeypatch, tmp_path):
    calls = {"n": 0}
    class FakeNav:
        def __init__(self, path):
            self.attempt_dir = path

    async def fake_trial(**kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("synthetic readiness exclusion")
        return {
            "nav": FakeNav(tmp_path / f"attempt_{calls['n']}"),
            "cleanup_error": None,
            "startup_attempts": 1,
            "startup_ready": {"ready": True},
        }

    def fake_extract(path: Path, capability: str, condition=None):
        return {
            "capability": capability,
            "condition": condition,
            "module_status": "completed",
            "verifier_overall": "PASS",
            "position_error_m": 0.1,
            "elapsed_time_s": 1.0,
        }

    monkeypatch.setattr(rel, "run_fresh_nav_trial", fake_trial)
    monkeypatch.setattr(rel, "extract_attempt_row", fake_extract)
    rows = []
    infra = []
    asyncio.run(rel.direct_condition(
        "Fresh TAKEOFF, near targets",
        trials=2,
        radius=3.0,
        out_rows=rows,
        infra_rows=infra,
        max_infra_exclusions=2,
        startup_retry_cooldown_s=0.0,
    ))
    assert len(rows) == 2
    assert len(infra) == 1
    assert calls["n"] == 3
    assert [r["trial"] for r in rows] == [1, 2]
