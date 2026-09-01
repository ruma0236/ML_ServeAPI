import io
from pathlib import Path
from types import SimpleNamespace

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


def test_s7_prometheus_refresh_uses_reload_only(monkeypatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr(s7_runner, "reload_prometheus", lambda: calls.append("reload"))
    monkeypatch.setattr(s7_runner, "wait_until", lambda predicate, timeout: True)
    result = s7_runner.refresh_prometheus_target("image", timeout=45)

    assert result["reload_count"] == 1
    assert result["container_restart_count"] == 0
    assert calls == ["reload"]


def test_s7_prometheus_cleanup_fails_closed_without_restart(monkeypatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr(s7_runner, "reload_prometheus", lambda: calls.append("reload"))
    monkeypatch.setattr(s7_runner, "wait_until", lambda predicate, timeout: False)

    with pytest.raises(S7RuntimeError, match="target_cleanup_timeout"):
        s7_runner.refresh_prometheus_target_absent(
            timeout=45,
            expected_baseline_target_count=5,
        )

    assert calls == ["reload"]


def test_s7_prometheus_cleanup_rejects_transient_zero_target_state(monkeypatch) -> None:
    monkeypatch.setattr(s7_runner, "prometheus_target_count", lambda: 0)
    monkeypatch.setattr(
        s7_runner,
        "prometheus_health",
        lambda: {"target_count": 0, "up_count": 0, "all_up": False},
    )

    assert s7_runner.prometheus_cleanup_restored(5) is False


def test_s7_prometheus_cleanup_requires_exact_restored_baseline(monkeypatch) -> None:
    monkeypatch.setattr(s7_runner, "prometheus_target_count", lambda: 0)
    monkeypatch.setattr(
        s7_runner,
        "prometheus_health",
        lambda: {"target_count": 5, "up_count": 5, "all_up": True},
    )

    assert s7_runner.prometheus_cleanup_restored(5) is True


def test_s7_cleanup_contract_fails_when_baseline_is_not_loaded() -> None:
    cleanup = {
        "holder_uid_exact": True,
        "holder_image_exact": True,
        "holder_replicas_exact": True,
        "source_model_sha256_exact": True,
        "source_candidate_exact": True,
        "source_cuda_inference": True,
        "gpu_lease_zero": True,
        "s7_target_cleanup": {"restored": True},
        "prometheus_baseline": {"target_count": 0, "up_count": 0, "all_up": False},
    }

    assert s7_runner.cleanup_contract_passed(cleanup, expected_baseline_target_count=5) is False


def test_s7_cleanup_contract_accepts_exact_source_lease_and_prometheus_restore() -> None:
    cleanup = {
        "holder_uid_exact": True,
        "holder_image_exact": True,
        "holder_replicas_exact": True,
        "source_model_sha256_exact": True,
        "source_candidate_exact": True,
        "source_cuda_inference": True,
        "gpu_lease_zero": True,
        "s7_target_cleanup": {"restored": True},
        "prometheus_baseline": {"target_count": 5, "up_count": 5, "all_up": True},
    }

    assert s7_runner.cleanup_contract_passed(
        cleanup,
        expected_baseline_target_count=5,
    )


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


def test_s7_diagnostic_manifest_override_is_explicit_and_non_acceptance(
    tmp_path: Path,
) -> None:
    manifest = tmp_path / "manifest.jsonl"
    manifest.write_text(
        "\n".join(
            '{"sample_id":"%s","content_sha256":"%s"}' % (index, "a" * 64) for index in range(6)
        )
        + "\n",
        encoding="utf-8",
        newline="\n",
    )
    asset = s7_runner.AssetSpec(
        family="image",
        port=1,
        manifest=manifest,
        manifest_sha256="b" * 64,
        dataset_version="visa-open-data-e35d93d5561f",
    )

    resolved, overrides = s7_runner.resolve_diagnostic_manifest_drift({"image": asset})

    assert resolved["image"].manifest_sha256 != asset.manifest_sha256
    assert overrides["image"]["acceptance_credit"] is False
    assert overrides["image"]["frozen_manifest_sha256"] == "b" * 64


def _service(process) -> s7_runner.ServiceProcess:
    return s7_runner.ServiceProcess(
        family="image",
        process=process,
        log_handle=io.StringIO(),
        log_path=Path("service.log"),
        base_url="http://127.0.0.1:1",
        run_uuid="00000000-0000-4000-8000-000000000001",
        root_created_at=1.0,
    )


def test_s7_service_stop_is_cooperative_and_bounded(monkeypatch) -> None:
    signals: list[int] = []
    process = SimpleNamespace(
        pid=101,
        poll=lambda: None,
        send_signal=lambda value: signals.append(value),
    )
    records = iter(
        [
            [{"pid": 101, "ppid": 1, "created_at": 1.0, "status": "running"}],
            [],
        ]
    )
    monkeypatch.setattr(
        s7_runner,
        "_live_service_processes",
        lambda service, known: next(records),
    )
    monkeypatch.setattr(
        s7_runner,
        "_send_graceful_signal",
        lambda service: signals.append(service.process.pid),
    )
    monkeypatch.setattr(s7_runner.time, "sleep", lambda _: None)

    result = s7_runner.stop_service(_service(process), residual_timeout=1.0)

    assert signals == [101]
    assert result["residual_process_count"] == 0
    assert result["forced_termination_attempts"] == 0
    assert result["automatic_retry_count"] == 0


def test_s7_service_residual_latches_without_force_or_followup(monkeypatch) -> None:
    process = SimpleNamespace(pid=102, poll=lambda: None, send_signal=lambda value: None)
    residual = {"pid": 102, "ppid": 1, "created_at": 1.0, "status": "running"}
    monkeypatch.setattr(
        s7_runner,
        "_live_service_processes",
        lambda service, known: [residual],
    )
    monkeypatch.setattr(s7_runner, "_send_graceful_signal", lambda service: None)

    with pytest.raises(s7_runner.S7ManualInterventionRequired) as caught:
        s7_runner.stop_service(_service(process), residual_timeout=0.0)

    evidence = caught.value.process_evidence
    assert evidence["residual_process_count"] == 1
    assert evidence["forced_termination_attempts"] == 0
    assert evidence["subsequent_probe_after_residual"] == 0
    assert evidence["automatic_retry_count"] == 0
    policy = s7_runner.lifecycle_followup_policy(evidence)
    assert policy == {
        "manual_latch": True,
        "automatic_restore_allowed": False,
        "subsequent_service_probe_allowed": False,
        "automatic_retry_count": 0,
        "forced_termination_attempts": 0,
    }


def test_s7_process_scan_uncertainty_latches_fail_closed(monkeypatch) -> None:
    process = SimpleNamespace(pid=103, poll=lambda: None, send_signal=lambda value: None)
    monkeypatch.setattr(
        s7_runner,
        "_live_service_processes",
        lambda service, known: (_ for _ in ()).throw(
            S7RuntimeError("s7_process_scope_scan_uncertain")
        ),
    )

    with pytest.raises(s7_runner.S7ManualInterventionRequired) as caught:
        s7_runner.stop_service(_service(process), residual_timeout=0.0)

    assert caught.value.process_evidence["residual_process_count"] == -1
    assert caught.value.process_evidence["forced_termination_attempts"] == 0


def test_s7_runner_contains_no_forced_termination_or_container_restart() -> None:
    source = (ROOT / "scripts/dev/run_s7_auxiliary_admission_experiment.py").read_text(
        encoding="utf-8"
    )

    for forbidden in ("taskkill", "TerminateProcess", ".kill(", '"docker", "restart"'):
        assert forbidden not in source
    assert "def restart_prometheus" not in source


def test_s7_v3_execution_remains_blocked_without_kernel_containment() -> None:
    assert s7_runner.S7_V3_KERNEL_CONTAINMENT_IMPLEMENTED is False


def test_s7_default_no_go_occurs_before_any_runtime_mutation(monkeypatch) -> None:
    args = SimpleNamespace(
        maintenance_approved=True,
        config=ROOT / "configs/s7_family_admission.toml",
        root=ROOT,
        families="image,vlm,llm",
        diagnostic=True,
        acknowledge_diagnostic_manifest_drift=True,
    )
    mutation_calls: list[str] = []
    monkeypatch.setattr(s7_runner, "parse_args", lambda: args)
    monkeypatch.setattr(s7_runner, "source_identity", lambda root: ("a" * 40, "branch"))
    monkeypatch.setattr(
        s7_runner,
        "scale_holder",
        lambda *args, **kwargs: mutation_calls.append("scale"),
    )
    monkeypatch.setattr(
        s7_runner,
        "acquire_scale_validation_gpu_lease",
        lambda *args, **kwargs: mutation_calls.append("lease"),
    )
    monkeypatch.setattr(
        s7_runner,
        "replace_mutable_json",
        lambda *args, **kwargs: mutation_calls.append("file_sd"),
    )

    with pytest.raises(S7RuntimeError, match="kernel_containment_required"):
        s7_runner.main()

    assert mutation_calls == []


def test_s7_failure_seal_is_exactly_once_and_followup_is_append_only(
    tmp_path: Path,
) -> None:
    first = s7_runner.failure_seal_payload(
        suite_id="suite-1",
        stage="first",
        error=RuntimeError("first"),
        manual_intervention_required=False,
        process_evidence=None,
        pre_mutation_checkpoint=None,
        restore_checkpoint=None,
    )
    second = s7_runner.failure_seal_payload(
        suite_id="suite-1",
        stage="second",
        error=RuntimeError("second"),
        manual_intervention_required=True,
        process_evidence={"residual_process_count": 1},
        pre_mutation_checkpoint=None,
        restore_checkpoint=None,
    )

    s7_runner.publish_failure_seal(tmp_path, first)
    original = (tmp_path / "failure-seal.json").read_bytes()
    s7_runner.publish_failure_seal(tmp_path, second)

    assert (tmp_path / "failure-seal.json").read_bytes() == original
    amendments = list(tmp_path.glob("failure-amendment-*.json"))
    assert len(amendments) == 1
    assert b'"followup_failure"' in amendments[0].read_bytes()


def test_s7_private_success_path_reentry_is_rejected_without_overwrite(
    tmp_path: Path,
) -> None:
    target = tmp_path / "private-evidence-index.json"
    s7_runner.canonical_write(target, {"attempt": 1})
    original = target.read_bytes()

    with pytest.raises(FileExistsError):
        s7_runner.canonical_write(target, {"attempt": 2})

    assert target.read_bytes() == original


def test_s7_restore_refuses_to_release_an_unowned_gpu_lease(tmp_path: Path, monkeypatch) -> None:
    owned = SimpleNamespace(run_id="owned", lease_id="lease-1", fencing_token="fence-1")
    other = SimpleNamespace(
        run_id="other",
        lease_id="lease-2",
        fencing_token="fence-2",
        state="active",
    )
    release_calls: list[str] = []
    monkeypatch.setattr(s7_runner, "read_active_gpu_lease", lambda: other)
    monkeypatch.setattr(
        s7_runner,
        "release_scale_validation_gpu_lease",
        lambda **kwargs: release_calls.append(kwargs["run_id"]),
    )

    with pytest.raises(
        s7_runner.S7ManualInterventionRequired,
        match="refuses_unowned_gpu_lease",
    ):
        s7_runner.restore_runtime_state(
            holder=SimpleNamespace(replicas=1),
            holder_scaled_down=False,
            target_path=tmp_path / "target.json",
            prior_target=None,
            owned_lease=owned,
        )

    assert release_calls == []
