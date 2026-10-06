from pathlib import Path

from src.evidence.contract import EvidenceContractValidator


def test_evidence_contract_detects_missing_files(tmp_path: Path):
    spec = tmp_path / "spec.yaml"
    spec.write_text(
        "evidence_contract:\n  required:\n    - input.json\n    - telemetry.csv\n",
        encoding="utf-8",
    )
    attempt = tmp_path / "attempt"
    attempt.mkdir()
    (attempt / "input.json").write_text("{}", encoding="utf-8")
    result = EvidenceContractValidator(spec).validate(attempt)
    assert result.complete is False
    assert result.missing == ("telemetry.csv",)


def test_evidence_contract_passes_when_complete(tmp_path: Path):
    spec = tmp_path / "spec.yaml"
    spec.write_text(
        "evidence_contract:\n  required:\n    - input.json\n    - telemetry.csv\n",
        encoding="utf-8",
    )
    attempt = tmp_path / "attempt"
    attempt.mkdir()
    (attempt / "input.json").write_text("{}", encoding="utf-8")
    (attempt / "telemetry.csv").write_text("timestamp\n", encoding="utf-8")
    assert EvidenceContractValidator(spec).validate(attempt).complete is True
