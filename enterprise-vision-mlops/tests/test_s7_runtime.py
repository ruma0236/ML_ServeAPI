from pathlib import Path

import pytest

from evm.scale_validation.s7_runtime import (
    S7RuntimeConfig,
    S7RuntimeError,
    analyze_s7_profiles,
)


ROOT = Path(__file__).resolve().parents[1]


def test_s7_runtime_contract_is_frozen() -> None:
    config = S7RuntimeConfig.from_path(ROOT / "configs/s7_family_admission.toml")

    assert config.repetitions == 3
    assert len(config.profile_ids) == 12
    assert config.max_short_bypass == 3
    assert config.image_accuracy_minimum == 0.5
    assert config.long_request_cost_units == {
        "image": 1000.0,
        "vlm": 1550.0,
        "llm": 768.0,
    }
    assert config.claim_boundary.startswith("Controlled family-specific")


def test_s7_prometheus_file_sd_job_is_versioned() -> None:
    payload = (ROOT / "monitoring/prometheus/prometheus.yml").read_text(encoding="utf-8")

    assert 'job_name: "evm-s7-family"' in payload
    assert "/etc/prometheus/targets/s7-family.json" in payload


def test_s7_analysis_rejects_missing_matrix() -> None:
    config = S7RuntimeConfig.from_path(ROOT / "configs/s7_family_admission.toml")

    result = analyze_s7_profiles([], config)

    assert result["runtime_verdict"] == "failed"
    assert not any(result["acceptance"].values())


def test_s7_config_rejects_changed_repetition(tmp_path: Path) -> None:
    raw = (ROOT / "configs/s7_family_admission.toml").read_text(encoding="utf-8")
    path = tmp_path / "invalid.toml"
    path.write_text(raw.replace("repetitions = 3", "repetitions = 2", 1), encoding="utf-8")

    with pytest.raises(S7RuntimeError, match="repetition_contract"):
        S7RuntimeConfig.from_path(path)
