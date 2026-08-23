from __future__ import annotations

import json
import threading
import time
from pathlib import Path

from scripts.dev.run_s4_gpu_batching_experiment import (
    RuntimeContext,
    advance_open_loop_schedule,
    build_gpu_api_command,
    private_evidence_index,
    summarize_point,
    trace_summary,
)
from evm.scale_validation.s4_runtime import S4RuntimeConfig, analyze_s4_results
from evm.scale_validation.s4_runtime import S4Point


def _config(root: Path) -> S4RuntimeConfig:
    source = Path("configs/s4_gpu_batching_runtime.toml")
    data_root = root / "data"
    paths = [
        "artifacts/scale_validation/s3/capacity-registry.json",
        "artifacts/scale_validation/s3/higgs-uci-2014-seed-20260817-v1/splits/train/features.npy",
        "artifacts/scale_validation/s3/higgs-uci-2014-seed-20260817-v1/splits/train/labels.npy",
        "artifacts/scale_validation/s3/higgs-uci-2014-seed-20260817-v1/splits/validation/features.npy",
        "artifacts/scale_validation/s3/higgs-uci-2014-seed-20260817-v1/splits/validation/labels.npy",
        "artifacts/scale_validation/s3/higgs-uci-2014-seed-20260817-v1/splits/replay/features.npy",
    ]
    for relative in paths:
        target = data_root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"test")
    copied = root / "s4.toml"
    copied.write_bytes(source.read_bytes())
    return S4RuntimeConfig.from_path(copied, data_root=data_root)


def _result(batch: int, delay: int, instances: int, mode: str, repetition: int) -> dict:
    throughput = 1000.0 + batch * 20 - delay * 2 + (instances - 1) * 50
    return {
        "point_id": f"{mode}-{batch}-{delay}-{instances}",
        "mode": mode,
        "repetition": repetition,
        "batch_size": batch,
        "max_delay_ms": delay,
        "instance_count": instances,
        "service_rps": throughput,
        "p95_ms": 10.0 + delay,
        "p99_ms": 15.0 + delay,
        "error_rate": 0.0,
        "queue_wait_p99_ms": float(delay),
        "peak_vram_bytes": 10_000_000 + batch * 1000 * instances,
        "gpu_utilization_percent_mean": 50.0,
        "temperature_celsius_max": 60.0,
        "power_watts_max": 150.0,
        "formed_batch_size_mean": float(batch),
        "fill_ratio_mean": 1.0,
        "oom_count": 0,
        "evidence_valid": True,
        "load_generator_valid": True,
        "operating_guardrail_passed": True,
    }


def test_s4_frozen_matrix_and_analysis_close_all_acceptance(tmp_path: Path) -> None:
    config = _config(tmp_path)
    assert config.closed_concurrency == 64
    assert config.open_service_rate_fraction == 0.70
    assert config.open_maximum_target_rps == 80.0
    assert config.public_dict()["preparation"] == {
        "closed_concurrency": 1,
        "warmup_seconds": 2.0,
        "measurement_seconds": 5.0,
        "cooldown_seconds": 1.0,
    }
    assert config.public_dict()["guardrails"] == {
        "maximum_error_rate": 0.01,
        "maximum_p99_ms": 250.0,
        "maximum_queue_wait_ms": 100.0,
        "hard_stop_p99_ms": 4500.0,
        "hard_stop_queue_wait_ms": 4000.0,
        "maximum_temperature_celsius": 84.0,
        "maximum_power_watts": 340.0,
        "require_zero_oom": True,
    }
    assert config.public_dict()["observability"] == {
        "trace_flush_timeout_seconds": 30.0,
        "trace_poll_interval_seconds": 0.25,
    }
    assert config.public_dict()["open_loop"] == {
        "service_rate_fraction": 0.70,
        "maximum_target_requests_per_second": 80.0,
        "repetitions": 3,
        "selection_reason": (
            "Use the lower of thirty-percent saturation headroom and the three-repeat "
            "calibrated 80 RPS ceiling before applying the fixed operating latency SLO."
        ),
    }
    results = [
        _result(batch, delay, 1, "matrix", repetition)
        for batch in config.batch_sizes
        for delay in config.max_delays_ms
        for repetition in range(1, 4)
    ]
    results.extend(_result(1, 0, 2, "instance-axis", repetition) for repetition in range(1, 4))
    results.extend(_result(32, 0, 1, "open-loop", repetition) for repetition in range(1, 4))

    analysis = analyze_s4_results(results, config)

    assert len(results) == 66
    assert analysis["runtime_verdict"] == "passed"
    assert all(analysis["acceptance"].values())
    assert len(analysis["aggregated_points"]) == 20
    assert analysis["instance_effect"]["instance_2_service_rps"] > 0
    assert analysis["s2_capacity_recalculation"]["calculated_depth"] > 0
    assert (
        analysis["s2_capacity_recalculation"][
            "validated_open_loop_service_rate_requests_per_second"
        ]
        > 0
    )
    json.dumps(analysis, allow_nan=False)


def test_s4_analysis_fails_without_open_loop_confirmation(tmp_path: Path) -> None:
    config = _config(tmp_path)
    results = [
        _result(batch, delay, 1, "matrix", repetition)
        for batch in config.batch_sizes
        for delay in config.max_delays_ms
        for repetition in range(1, 4)
    ]
    results.extend(_result(1, 0, 2, "instance-axis", repetition) for repetition in range(1, 4))

    analysis = analyze_s4_results(results, config)

    assert analysis["acceptance"]["S4-AC-02"] is False
    assert analysis["acceptance"]["S4-AC-04"] is False
    assert analysis["runtime_verdict"] == "failed"


def test_s4_open_loop_identity_must_match_selected_saturation_point(tmp_path: Path) -> None:
    config = _config(tmp_path)
    results = [
        _result(batch, delay, 1, "matrix", repetition)
        for batch in config.batch_sizes
        for delay in config.max_delays_ms
        for repetition in range(1, 4)
    ]
    results.extend(_result(1, 0, 2, "instance-axis", repetition) for repetition in range(1, 4))
    results.extend(_result(4, 0, 1, "open-loop", repetition) for repetition in range(1, 4))

    analysis = analyze_s4_results(results, config)

    assert analysis["selection_contract"]["open_loop_matches_selected"] is False
    assert analysis["acceptance"]["S4-AC-02"] is False
    assert analysis["acceptance"]["S4-AC-04"] is False


def test_s4_analysis_selects_saturation_candidate_then_defers_slo_to_open_loop(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    results = [
        _result(batch, delay, 1, "matrix", repetition)
        for batch in config.batch_sizes
        for delay in config.max_delays_ms
        for repetition in range(1, 4)
    ]
    for result in results:
        if result["batch_size"] == 32 and result["max_delay_ms"] == 0:
            result["service_rps"] = 100_000.0
            result["p99_ms"] = 300.0
            result["queue_wait_p99_ms"] = 150.0
            result["operating_guardrail_passed"] = False

    analysis = analyze_s4_results(results, config)

    selected = analysis["selected_operating_point"]
    assert selected is not None
    assert (selected["batch_size"], selected["max_delay_ms"]) == (32, 0)
    assert analysis["acceptance"]["S4-AC-02"] is False
    assert analysis["selection_contract"]["closed_loop_role"] == (
        "capacity_and_pareto_candidate_discovery"
    )


def test_s4_open_loop_must_pass_operating_latency_guardrail(tmp_path: Path) -> None:
    config = _config(tmp_path)
    results = [
        _result(batch, delay, 1, "matrix", repetition)
        for batch in config.batch_sizes
        for delay in config.max_delays_ms
        for repetition in range(1, 4)
    ]
    results.extend(_result(1, 0, 2, "instance-axis", repetition) for repetition in range(1, 4))
    open_results = [_result(32, 0, 1, "open-loop", repetition) for repetition in range(1, 4)]
    open_results[2]["operating_guardrail_passed"] = False
    results.extend(open_results)

    analysis = analyze_s4_results(results, config)

    assert analysis["acceptance"]["S4-AC-02"] is False
    assert analysis["acceptance"]["S4-AC-04"] is False
    assert analysis["runtime_verdict"] == "failed"


def test_s4_open_loop_schedule_skips_missed_releases_without_catch_up() -> None:
    scheduled, following, skipped = advance_open_loop_schedule(
        now=1.055,
        next_release=1.0,
        interval=0.01,
    )

    assert skipped == 5
    assert 0 <= 1.055 - scheduled < 0.01
    assert following > 1.055


def test_s4_open_loop_summary_fails_closed_on_under_delivered_profile(tmp_path: Path) -> None:
    config = _config(tmp_path)
    measurement = {
        "duration_seconds": 30.0,
        "configured_concurrency": 64,
        "statuses": {"200": 2400},
        "latencies_ms": [20.0] * 2400,
        "queue_waits_ms": [2.0] * 2400,
        "formed_batch_sizes": [4] * 2400,
        "h2d_ms": [0.1] * 2400,
        "inference_ms": [0.2] * 2400,
        "d2h_ms": [0.1] * 2400,
        "allocated_vram_bytes": [1] * 2400,
        "reserved_vram_bytes": [1] * 2400,
        "peak_vram_bytes": [1] * 2400,
        "resource_samples": [],
        "load_generator": {
            "target_requests_per_second": 100.0,
            "target_release_count": 3000,
            "released_count": 2400,
            "actual_offered_requests_per_second": 80.0,
            "skipped_release_count": 600,
            "release_lags_ms": [1.0] * 2400,
        },
    }

    summary = summarize_point(
        point=S4Point(8, 5, 1, "open-loop"),
        repetition=1,
        measurement=measurement,
        drain={
            "evm_s4_gpu_batch_queue_depth": 0.0,
            "evm_s4_gpu_batch_queue_bytes": 0.0,
            "evm_s4_gpu_batch_in_flight": 0.0,
        },
        trace={"missing_count": 0},
        config=config,
        prometheus_recovery_wait_seconds=0.0,
    )

    assert summary["offered_rate_delivery_ratio"] == 0.8
    assert summary["load_generator_valid"] is False
    assert summary["evidence_valid"] is False


def test_s4_gpu_api_uses_supported_isolated_file_store(tmp_path: Path) -> None:
    config = _config(tmp_path)
    data_root = config.model_root.parents[3]
    registry_path = config.model_root / "registry.json"
    context = RuntimeContext(
        image="s4-runtime:test",
        network="test-network",
        source_revision="a" * 40,
        source_branch="codex/test",
        registry_path=registry_path,
        data_root=data_root,
        lease_run_id="s4-inference-test",
        lease_id="lease-test",
        fencing_token="fence-test",
        private_root=tmp_path / "private",
        trace_path=tmp_path / "traces.json",
    )

    command = build_gpu_api_command(
        context,
        config,
        S4Point(batch_size=1, max_delay_ms=0, instance_count=1, mode="smoke"),
    )

    assert "EVM_CONTROL_PLANE_STORE_MODE=file" in command
    assert "EVM_CONTROL_PLANE_STORE_MODE=json" not in command
    assert "--rm" not in command


def test_s4_private_index_excludes_its_own_generated_file(tmp_path: Path) -> None:
    (tmp_path / "point.json").write_text("{}\n", encoding="utf-8", newline="\n")
    (tmp_path / "private-evidence-index.json").write_text("stale\n", encoding="utf-8", newline="\n")

    index = private_evidence_index(tmp_path)

    assert index["artifact_count"] == 1
    assert [entry["path"] for entry in index["entries"]] == ["point.json"]


def test_s4_trace_summary_waits_for_complete_exported_chain(tmp_path: Path) -> None:
    trace_id = "a" * 32
    trace_path = tmp_path / "traces.json"
    trace_path.write_bytes(b"")

    def write_trace() -> None:
        time.sleep(0.05)
        payload = {
            "resourceSpans": [
                {
                    "scopeSpans": [
                        {
                            "spans": [
                                {
                                    "traceId": trace_id,
                                    "name": "POST /control-panel/v1/scenario-workloads/gpu-batch-probes/predict",
                                },
                                {"traceId": trace_id, "name": "s4.gpu_batch.worker"},
                            ]
                        }
                    ]
                }
            ]
        }
        trace_path.write_text(json.dumps(payload) + "\n", encoding="utf-8")

    writer = threading.Thread(target=write_trace)
    writer.start()
    summary = trace_summary(
        trace_path,
        offset=0,
        expected={trace_id},
        timeout_seconds=0.5,
        poll_interval_seconds=0.01,
    )
    writer.join()

    assert summary["complete_count"] == 1
    assert summary["missing_count"] == 0
    assert summary["flush_completed"] is True
    assert summary["flush_poll_count"] > 1
