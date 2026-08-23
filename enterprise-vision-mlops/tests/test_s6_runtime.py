from __future__ import annotations

import json
from pathlib import Path

from evm.scale_validation.s6_runtime import (
    S6RuntimeConfig,
    analyze_s6_results,
    deterministic_traceparent,
    summarize_latencies,
)


CONFIG = Path("configs/s6_rolling_handoff.toml")


def _config(tmp_path: Path) -> S6RuntimeConfig:
    source_paths = (
        "artifacts/w7/lifecycle_runs/lifecycle-20260805T104249-7d184e13/readiness.json",
        "artifacts/w7/lifecycle_runs/lifecycle-20260805T104249-7d184e13/model/lifecycle-20260805T104249-7d184e13/efficientnet-b0-profile-standard-b0-manual-tuning-v11/candidate_summary.json",
        "artifacts/w7/lifecycle_runs/lifecycle-20260805T104249-7d184e13/validation/release-submission.json",
        "artifacts/w7/lifecycle_runs/lifecycle-20260805T104249-7d184e13/model/lifecycle-20260805T104249-7d184e13/efficientnet-b0-profile-standard-b0-manual-tuning-v11/model.pt",
    )
    relative_paths = ("r.json", "c.json", "s.json", "m.pt")
    payload = CONFIG.read_text(encoding="utf-8")
    for source, replacement in zip(source_paths, relative_paths, strict=True):
        payload = payload.replace(source, replacement)
    local_config = tmp_path / "s6.toml"
    local_config.write_text(payload, encoding="utf-8")
    for relative in relative_paths:
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"fixture")
    return S6RuntimeConfig.from_path(local_config, data_root=tmp_path)


def _api_result(repetition: int) -> dict[str, object]:
    return {
        "repetition": repetition,
        "logical_requests": 500,
        "attempts": 505,
        "client_success": 500,
        "database_accepted": 500,
        "database_terminal": 500,
        "accepted_loss": 0,
        "duplicate_effects": 0,
        "error_rate": 0.0,
        "trace_identity_matches": 500,
        "trace_expected": 20,
        "trace_observed": 20,
        "trace_complete": True,
        "drain_event_count": 2,
        "rollout_seconds": 9.0,
        "maximum_drain_seconds": 0.2,
        "p99_ms": 130.0,
        "prometheus_up": True,
        "cleanup_passed": True,
    }


def _gpu_result(repetition: int) -> dict[str, object]:
    return {
        "repetition": repetition,
        "status": "passed",
        "candidate_gate_passed": True,
        "approval_consumed_once": True,
        "zero_owner_overlap": True,
        "target_identity_exact": True,
        "rollback_exact": True,
        "target_cuda_inference": True,
        "source_cuda_inference_restored": True,
        "prometheus_restored": True,
        "source_to_target_interruption_seconds": 12.0,
        "target_to_source_interruption_seconds": 10.0,
        "target_p99_ms": 2.0,
        "target_inference_count": 30,
    }


def test_s6_config_freezes_two_replica_and_three_repeat_contract(tmp_path: Path) -> None:
    config = _config(tmp_path)

    assert config.api.replicas == 2
    assert config.api.repetitions == 3
    assert config.api.rollout_offset_seconds == 5.0
    assert config.api.trace_sample_every == 25
    assert config.rolling.max_unavailable == 0
    assert config.rolling.max_surge == 1
    assert config.gpu_handoff.repetitions == 3
    assert config.gpu_handoff.calibration_inference_requests == 30
    assert "zero-downtime GPU HA" in config.claim_boundary
    json.dumps(config.public_dict(), allow_nan=False)


def test_s6_analysis_recomputes_all_four_acceptance_criteria(tmp_path: Path) -> None:
    config = _config(tmp_path)
    calibration = _gpu_result(0)
    api = [_api_result(index) for index in range(1, 4)]
    gpu = [_gpu_result(index) for index in range(1, 4)]

    analysis = analyze_s6_results(
        api_repetitions=api,
        gpu_calibration=calibration,
        gpu_repetitions=gpu,
        config=config,
    )

    assert analysis["status"] == "passed"
    assert all(
        item["status"] == "passed" for item in analysis["acceptance"].values()
    )


def test_s6_analysis_fails_closed_on_lost_accepted_request(tmp_path: Path) -> None:
    config = _config(tmp_path)
    api = [_api_result(index) for index in range(1, 4)]
    api[1]["client_success"] = 499
    api[1]["accepted_loss"] = 1

    analysis = analyze_s6_results(
        api_repetitions=api,
        gpu_calibration=_gpu_result(0),
        gpu_repetitions=[_gpu_result(index) for index in range(1, 4)],
        config=config,
    )

    assert analysis["status"] == "failed"
    assert analysis["acceptance"]["S6-AC-01"]["status"] == "failed"


def test_s6_trace_and_latency_helpers_are_deterministic() -> None:
    sampled, trace_id = deterministic_traceparent("request-1", sampled=True)
    unsampled, same_trace_id = deterministic_traceparent("request-1", sampled=False)

    assert sampled.endswith("-01")
    assert unsampled.endswith("-00")
    assert trace_id == same_trace_id
    assert summarize_latencies([1.0, 2.0, 3.0, 4.0])["p99_ms"] > 3.9
