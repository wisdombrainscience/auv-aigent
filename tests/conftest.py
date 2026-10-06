"""Test-suite environment isolation.

The deployed application intentionally loads runtime configuration from
``uav.env`` (for example ``UAV_DATA_DIR``). Unit tests must never reuse a
real user's persistent session/evidence directory, simulator backend, or GUI
settings.  Clear those variables before every test; individual tests can still
set a value explicitly with pytest's monkeypatch fixture.
"""
from __future__ import annotations

import os

import pytest


_RUNTIME_ENV_VARS = (
    "UAV_DATA_DIR",
    "UAV_GAZEBO_GUI",
    "UAV_GAZEBO_SOFTWARE_RENDERING",
    "UAV_GZ_WORLD",
    "UAV_PORT",
    "UAV_SIM_BACKEND",
    "UAV_SIM_CONTAINER",
    "UAV_SIM_IMAGE",
    "UAV_SIM_MODEL",
)


@pytest.fixture(autouse=True)
def _isolate_runtime_environment(monkeypatch: pytest.MonkeyPatch):
    for name in _RUNTIME_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    yield
