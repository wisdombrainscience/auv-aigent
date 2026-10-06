from pathlib import Path


PROJECT = Path(__file__).resolve().parents[2]


def test_step37a_uses_generic_executor_not_direct_nav001_components() -> None:
    source = (PROJECT / "orchestration/web_experiment.py").read_text(encoding="utf-8")
    assert "CapabilityExecutor" in source
    assert "build_default_registry" in source
    assert "GoToTargetModule(" not in source
    assert "GoToTargetVerifier(" not in source


def test_catalog_is_the_nav001_runtime_binding_source() -> None:
    source = (PROJECT / "src/capabilities/catalog.py").read_text(encoding="utf-8")
    assert 'capability_id="NAV-001"' in source
    assert "GoToTargetModule" in source
    assert "GoToTargetVerifier" in source
