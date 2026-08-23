from pathlib import Path

import pytest
import scripts.dev.run_s7_auxiliary_admission_experiment as s7_runner

from evm.scale_validation.s7_runtime import (
    S7RuntimeConfig,
    S7RuntimeError,
    analyze_s7_profiles,
    host_image_data_environment,
    restore_file_sd_target,
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


def test_s7_host_image_service_does_not_remap_to_container_mount(tmp_path: Path) -> None:
    environment = host_image_data_environment(tmp_path)

    assert environment["EVM_HOST_DATA_ROOT"] == str(tmp_path.resolve())
    assert environment["EVM_DATA_MOUNT_ROOT"] == str(tmp_path.resolve())


def test_s7_file_sd_cleanup_keeps_explicit_empty_target_set(tmp_path: Path) -> None:
    target = tmp_path / "targets/s7-family.json"

    restore_file_sd_target(target, None)

    assert target.read_bytes() == b"[]\n"


def test_s7_prometheus_refresh_restarts_when_file_watch_does_not_converge(
    monkeypatch,
) -> None:
    calls: list[str] = []
    waits = iter([False, True])
    monkeypatch.setattr(s7_runner, "reload_prometheus", lambda: calls.append("reload"))
    monkeypatch.setattr(s7_runner, "restart_prometheus", lambda: calls.append("restart"))
    monkeypatch.setattr(s7_runner, "wait_until", lambda predicate, timeout: next(waits))

    result = s7_runner.refresh_prometheus_target("image", timeout=45)

    assert result["restart_used"] is True
    assert calls == ["reload", "restart"]


def test_s7_prometheus_cleanup_restarts_when_stale_target_remains(monkeypatch) -> None:
    calls: list[str] = []
    waits = iter([False, True])
    monkeypatch.setattr(s7_runner, "reload_prometheus", lambda: calls.append("reload"))
    monkeypatch.setattr(s7_runner, "restart_prometheus", lambda: calls.append("restart"))
    monkeypatch.setattr(s7_runner, "wait_until", lambda predicate, timeout: next(waits))

    result = s7_runner.refresh_prometheus_target_absent(timeout=45)

    assert result["restart_used"] is True
    assert calls == ["reload", "restart"]


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
