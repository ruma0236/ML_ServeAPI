from __future__ import annotations

import json
import runpy
from collections import Counter
from itertools import groupby
from pathlib import Path

import pytest

from evm.control_panel.scenario_workloads import (
    ScenarioWorkloadError,
    acquire_scale_validation_gpu_lease,
    release_scale_validation_gpu_lease,
)
from evm.scale_validation.x1_resume_testbed import (
    EXPECTED_MODELS,
    EXPECTED_PROMETHEUS_JOBS,
    X1ResumeConfig,
    X1ResumeTestbedError,
    canonical_sha256,
    canonical_write,
    deterministic_model_schedule,
    generate_report,
    prometheus_baseline_ready,
    request_interval_overlap,
    sha256_file,
    summarize_requests,
    triton_trace_compute_counts,
    validate_evidence,
    wait_for_prometheus_baseline,
)

# Importing the host runner is intentionally avoided; assert the frozen Triton
# 25.08 token form directly from its committed source.


ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "configs/s8_v4_x1_resume_testbed_v1.toml"


def config() -> X1ResumeConfig:
    return X1ResumeConfig.from_path(CONFIG)


def synthetic_records(model_mix: dict[str, float]) -> list[dict[str, object]]:
    return [
        {
            "request_id": f"synthetic-{model_id}",
            "model_id": model_id,
            "outcome": "completed",
            "status": 200,
            "started_ns": 10,
            "finished_ns": 20,
            "latency_ms": 1.0,
            "queue_wait_ms": 0.1,
        }
        for model_id, fraction in model_mix.items()
        if fraction > 0
    ]


def metric_payload(model_mix: dict[str, float] | None = None) -> dict[str, object]:
    model_mix = model_mix or {model: 0.25 for model in EXPECTED_MODELS}
    records = synthetic_records(model_mix)
    return summarize_requests(
        offered=24_000,
        admitted=len(records),
        local_admission_rejected=24_000 - len(records),
        records=records,
        measurement_seconds=30,
        measurement_end_ns=1_000,
        drain_seconds=0.1,
        model_mix=model_mix,
    )


def complete_payload() -> dict[str, object]:
    cfg = config()
    q0 = [
        {
            "model_id": model,
            "artifact_sha256": "a" * 64,
            "config_sha256": "b" * 64,
            "triton_config_readback": {"instance_group": [{"kind": "KIND_GPU"}]},
            "cuda_activity_observed": True,
            "cpu_fallback_observed": False,
            "triton_gpu_instance_proof": True,
            "triton_compute_delta": 1.0,
            "isolated_gpu_busy_samples": 1,
            "isolated_request_count": 64,
            "triton_trace_compute_start_count": 1,
        }
        for model in EXPECTED_MODELS
    ]
    runs = []
    for cell in cfg.cells:
        for repetition in range(1, cell.repetitions + 1):
            records = synthetic_records(dict(cell.model_mix))
            active_count = len(records)
            formed_batch = cell.cell_id == "balanced-concurrent-batch-on"
            runs.append(
                {
                    "attempt_id": f"{cell.cell_id}-{repetition}",
                    "cell_id": cell.cell_id,
                    "repetition": repetition,
                    "batching": cell.batching,
                    "model_mix": dict(cell.model_mix),
                    "client_topology": {
                        "lanes": cell.client_lanes,
                        "workers": cell.client_workers,
                    },
                    "load_contract": {
                        "target_offered_rps": cfg.offered_rps,
                        "minimum_offered_rate_attainment": cfg.minimum_offered_rate_attainment,
                        "matched_load_relative_tolerance": cfg.matched_load_relative_tolerance,
                        "warmup_seconds": cfg.warmup_seconds,
                        "measurement_seconds": cfg.measurement_seconds,
                    },
                    "metrics": metric_payload(dict(cell.model_mix)),
                    "triton_execution_proved": True,
                    "cpu_fallback_observed": False,
                    "cross_model_request_overlap_required": cell.client_workers > 1
                    and len(cell.model_mix) > 1,
                    "cross_model_request_overlap": request_interval_overlap(records),
                    "batching_proof": {
                        "inference_count_delta": float(active_count * (2 if formed_batch else 1)),
                        "execution_count_delta": float(active_count),
                        "formed_batch_observed": formed_batch,
                        "formed_mean_batch_size": 2.0 if formed_batch else 1.0,
                    },
                }
            )
    return {
        "schema_version": "evm.s8_v4.x1_resume_testbed.v1",
        "suite_id": "x1-resume-test",
        "status": "complete",
        "claim_class": "preliminary_controlled_testbed",
        "credit": "non_credit",
        "canonical_x1": False,
        "acceptance_credit": False,
        "config_sha256": cfg.sha256,
        "q0": q0,
        "runs": runs,
        "cleanup": {
            "container_absent": True,
            "ports_absent": True,
            "gpu_lease_absent": True,
            "triton_gpu_process_residue": [],
            "b0_identity_restored": True,
            "b0_cuda_restored": True,
            "queue_active_zero": True,
            "queue_leased_zero": True,
            "queue_outcome_unknown_zero": True,
            "prometheus_5_of_5": True,
            "prometheus_exact_jobs_restored": True,
            "errors": [],
        },
        "cleanup_evidence": {
            "path": "cleanup.json",
            "bytes": 1,
            "sha256": "c" * 64,
            "final_checks_sha256": "d" * 64,
        },
        "profiler": {"kernel_overlap_proved": False},
        "claim_boundary": cfg.claim_boundary,
    }


def test_config_freezes_non_credit_matrix_and_honest_driver_scope() -> None:
    cfg = config()
    assert cfg.expected_physical_runs == 22
    assert len(cfg.cells) == 10
    assert "not deployed API replicas" in cfg.claim_boundary
    assert "kernel-overlap evidence unless a profiler directly proves overlap" in cfg.claim_boundary
    runner = (ROOT / "scripts/dev/run_s8_v4_x1_resume_testbed.py").read_text(encoding="utf-8")
    assert "--trace-config=mode=triton" in runner
    assert "--trace-config=triton,file=/evidence/triton-trace.json" in runner
    assert "--trace-config=rate=64" in runner
    assert "trace_enabled=False" in runner


def test_prometheus_cleanup_waits_for_the_exact_restored_baseline() -> None:
    expected = list(EXPECTED_PROMETHEUS_JOBS)
    healthy = {"jobs": expected, "total": 5, "up": 5}
    assert prometheus_baseline_ready(healthy, expected) is True
    for mutation in (
        {**healthy, "up": 4},
        {**healthy, "total": 4},
        {**healthy, "jobs": expected[:-1]},
        {**healthy, "jobs": [*expected[:-1], "wrong-job"]},
        {**healthy, "up": True},
    ):
        assert prometheus_baseline_ready(mutation, expected) is False

    snapshots = iter(({**healthy, "up": 4}, healthy))
    tick = [0.0]

    def advance(seconds: float) -> None:
        tick[0] += seconds

    budgets = []

    def health(remaining: float) -> dict[str, object]:
        budgets.append(remaining)
        return next(snapshots)

    result = wait_for_prometheus_baseline(
        health,
        expected,
        timeout_seconds=3.0,
        poll_interval_seconds=1.0,
        monotonic=lambda: tick[0],
        sleep=advance,
        observed_at=lambda: f"t={tick[0]}",
    )
    assert result[0] == healthy
    assert result[1] == 1.0
    assert [sample["snapshot"]["up"] for sample in result[2]] == [4, 5]
    assert [sample["state"] for sample in result[2]] == ["retryable_4_of_5", "ready"]
    assert result[3] is True
    assert result[4] == "ready"
    assert budgets == [3.0, 2.0]

    runner = (ROOT / "scripts/dev/run_s8_v4_x1_resume_testbed.py").read_text(
        encoding="utf-8"
    )
    assert "def wait_prometheus_restore(" in runner
    assert "wait_prometheus_restore(config.cleanup_timeout_seconds)" in runner
    assert "lambda remaining: prometheus_health(timeout=min(10.0, remaining))" in runner
    assert 'final_checks["prometheus_restore_samples"]' in runner


def test_prometheus_cleanup_persistent_4_of_5_times_out_fail_closed() -> None:
    expected = list(EXPECTED_PROMETHEUS_JOBS)
    unhealthy = {"jobs": expected, "total": 5, "up": 4}
    tick = [0.0]

    def advance(seconds: float) -> None:
        tick[0] += seconds

    snapshot, elapsed, samples, ready, reason = wait_for_prometheus_baseline(
        lambda _remaining: unhealthy,
        expected,
        timeout_seconds=2.0,
        poll_interval_seconds=1.0,
        monotonic=lambda: tick[0],
        sleep=advance,
        observed_at=lambda: f"t={tick[0]}",
    )
    assert snapshot == unhealthy
    assert elapsed == 2.0
    assert len(samples) == 2
    assert ready is False
    assert reason == "timeout"
    assert prometheus_baseline_ready(snapshot, expected) is False


@pytest.mark.parametrize(
    "malformed",
    [
        {
            "jobs": [
                "evm-api",
                "evm-b0-production",
                "evm-otel-collector",
                "evm-task-queue-worker",
                "wrong-job",
            ],
            "total": 5,
            "up": 5,
        },
        {"jobs": "not-a-list", "total": 5, "up": 5},
    ],
    ids=["wrong-job-set", "malformed-job-set"],
)
def test_prometheus_cleanup_job_set_mismatch_fails_without_retry(
    malformed: dict[str, object],
) -> None:
    tick = [0.0]
    calls = [0]

    def health(_remaining: float) -> dict[str, object]:
        calls[0] += 1
        return malformed

    _snapshot, elapsed, samples, ready, reason = wait_for_prometheus_baseline(
        health,
        EXPECTED_PROMETHEUS_JOBS,
        timeout_seconds=2.0,
        poll_interval_seconds=1.0,
        monotonic=lambda: tick[0],
        sleep=lambda seconds: tick.__setitem__(0, tick[0] + seconds),
        observed_at=lambda: "t=0",
    )
    assert calls == [1]
    assert elapsed == 0.0
    assert len(samples) == 1
    assert ready is False
    assert reason == "invalid_snapshot"


def test_prometheus_cleanup_slow_healthy_probe_cannot_cross_deadline() -> None:
    tick = [0.0]

    def slow_healthy(remaining: float) -> dict[str, object]:
        tick[0] += remaining + 0.01
        return {"jobs": list(EXPECTED_PROMETHEUS_JOBS), "total": 5, "up": 5}

    _snapshot, elapsed, samples, ready, reason = wait_for_prometheus_baseline(
        slow_healthy,
        EXPECTED_PROMETHEUS_JOBS,
        timeout_seconds=2.0,
        poll_interval_seconds=1.0,
        monotonic=lambda: tick[0],
        sleep=lambda _seconds: None,
        observed_at=lambda: "t=0",
    )
    assert elapsed > 2.0
    assert samples[-1]["state"] == "ready"
    assert ready is False
    assert reason == "deadline_exceeded"


def test_prometheus_cleanup_http_error_cannot_retry_into_healthy() -> None:
    calls = [0]

    def error_then_healthy(_remaining: float) -> dict[str, object]:
        calls[0] += 1
        if calls[0] == 1:
            raise TimeoutError("probe timeout")
        return {"jobs": list(EXPECTED_PROMETHEUS_JOBS), "total": 5, "up": 5}

    result = wait_for_prometheus_baseline(
        error_then_healthy,
        EXPECTED_PROMETHEUS_JOBS,
        timeout_seconds=2.0,
        poll_interval_seconds=1.0,
        monotonic=lambda: 0.0,
        sleep=lambda _seconds: None,
        observed_at=lambda: "t=0",
    )
    assert calls == [1]
    assert result[3] is False
    assert result[4] == "probe_error"


def test_runner_prometheus_preflight_accepts_only_exact_5_of_5() -> None:
    runner = runpy.run_path(
        str(ROOT / "scripts/dev/run_s8_v4_x1_resume_testbed.py"),
        run_name="x1_resume_runner_preflight_test",
    )
    assert_preflight = runner["assert_prometheus_preflight"]
    healthy = {"jobs": list(EXPECTED_PROMETHEUS_JOBS), "total": 5, "up": 5}
    assert_preflight(healthy)

    invalid = (
        {**healthy, "jobs": [*EXPECTED_PROMETHEUS_JOBS[:-1], "wrong-job"]},
        {"jobs": list(EXPECTED_PROMETHEUS_JOBS[:-1]), "total": 4, "up": 4},
        {
            "jobs": [*EXPECTED_PROMETHEUS_JOBS[:-1], EXPECTED_PROMETHEUS_JOBS[0]],
            "total": 5,
            "up": 5,
        },
    )
    for snapshot in invalid:
        with pytest.raises(X1ResumeTestbedError, match="x1_resume_prometheus_preflight"):
            assert_preflight(snapshot)


def test_hot_mix_fairness_uses_normalized_attainment() -> None:
    records = []
    counts = {
        "higgs_logistic_regression": 10,
        "higgs_gaussian_nb": 10,
        "higgs_tiny_mlp": 10,
        "criteo_dlrm_lite": 70,
    }
    for model, count in counts.items():
        for index in range(count):
            records.append(
                {
                    "request_id": f"{model}-{index}",
                    "model_id": model,
                    "outcome": "completed",
                    "latency_ms": 1.0,
                    "queue_wait_ms": 0.1,
                    "finished_ns": 1,
                }
            )
    result = summarize_requests(
        offered=100,
        admitted=100,
        local_admission_rejected=0,
        records=records,
        measurement_seconds=1,
        measurement_end_ns=2,
        drain_seconds=0.1,
        model_mix={model: count / 100 for model, count in counts.items()},
    )
    assert result["normalized_attainment_jain_fairness"] == pytest.approx(1.0)
    assert result["raw_throughput_jain_fairness"] < 0.6


def test_balanced_and_hot_schedules_are_weighted_fair_not_contiguous_bursts() -> None:
    balanced = deterministic_model_schedule({model: 0.25 for model in EXPECTED_MODELS})
    assert balanced[:8] == EXPECTED_MODELS + EXPECTED_MODELS
    hot = deterministic_model_schedule(
        {
            "higgs_logistic_regression": 0.10,
            "higgs_gaussian_nb": 0.10,
            "higgs_tiny_mlp": 0.10,
            "criteo_dlrm_lite": 0.70,
        }
    )
    assert Counter(hot) == Counter(
        {
            "higgs_logistic_regression": 10,
            "higgs_gaussian_nb": 10,
            "higgs_tiny_mlp": 10,
            "criteo_dlrm_lite": 70,
        }
    )
    assert max(len(list(group)) for _model, group in groupby(hot)) < 25


def test_triton_trace_parser_joins_model_and_case_insensitive_compute_timestamp(
    tmp_path: Path,
) -> None:
    trace = tmp_path / "trace.json"
    trace.write_text(
        "["
        '{"id":1,"model_name":"higgs_logistic_regression","request_id":"q0-lr"},'
        '{"id":1,"timestamps":[{"name":"request_start","ns":1},'
        '{"name":"compute_start","ns":2}]},'
        '{"id":2,"model_name":"criteo_dlrm_lite","request_id":"q0-dlrm"},'
        '{"id":2,"timestamps":[{"name":"COMPUTE_START","ns":3}]}'
        "]",
        encoding="utf-8",
    )
    counts = triton_trace_compute_counts(trace)
    assert counts["higgs_logistic_regression"] == 1
    assert counts["criteo_dlrm_lite"] == 1
    assert counts["higgs_gaussian_nb"] == 0


def test_triton_trace_parser_rejects_malformed_or_unbound_compute(tmp_path: Path) -> None:
    malformed = tmp_path / "malformed.json"
    malformed.write_text(
        '{"id":1,"model_name":"higgs_logistic_regression"}\n{broken', encoding="utf-8"
    )
    with pytest.raises(X1ResumeTestbedError, match="trace_json"):
        triton_trace_compute_counts(malformed)
    unbound = tmp_path / "unbound.json"
    unbound.write_text('[{"id":9,"timestamps":[{"name":"COMPUTE_START"}]}]', encoding="utf-8")
    with pytest.raises(X1ResumeTestbedError, match="trace_unbound_compute"):
        triton_trace_compute_counts(unbound)


def test_complete_evidence_preserves_claim_boundary() -> None:
    payload = complete_payload()
    result = validate_evidence(payload, config())
    assert result["physical_run_count"] == 22


def test_q0_cpu_fallback_or_missing_trace_fails_closed() -> None:
    payload = complete_payload()
    payload["q0"][0]["cpu_fallback_observed"] = True
    payload["q0"][0]["triton_trace_compute_start_count"] = 0
    with pytest.raises(X1ResumeTestbedError, match="q0_cuda_contract|q0_trace_compute_start"):
        validate_evidence(payload, config())


def test_hot_mix_requires_every_non_hot_model_to_progress() -> None:
    payload = complete_payload()
    hot = next(item for item in payload["runs"] if item["cell_id"] == "hot-dlrm-l2w4")
    hot["metrics"]["per_model"]["higgs_logistic_regression"]["window_completed"] = 0
    with pytest.raises(X1ResumeTestbedError, match="hot_non_hot_progress"):
        validate_evidence(payload, config())


def test_metric_recomputation_and_resume_success_errors_fail_closed() -> None:
    payload = complete_payload()
    run = payload["runs"][0]
    run["metrics"]["throughput_rps"] = 999.0
    run["metrics"]["admitted_cohort_http_5xx"] = 1
    with pytest.raises(
        X1ResumeTestbedError, match="window_metric_recompute|resume_success_errors_or_loss"
    ):
        validate_evidence(payload, config())


def test_low_offered_load_and_unmatched_comparison_load_fail_closed() -> None:
    payload = complete_payload()
    balanced = next(item for item in payload["runs"] if item["cell_id"] == "balanced-serial")
    balanced["metrics"]["offered"] = 4
    balanced["metrics"]["local_admission_rejected"] = 3
    balanced["metrics"]["actual_offered_rps"] = 4 / 30
    with pytest.raises(X1ResumeTestbedError, match="offered_load_attainment"):
        validate_evidence(payload, config())

    payload = complete_payload()
    for item in payload["runs"]:
        if item["cell_id"] == "balanced-serial":
            item["metrics"]["offered"] = 21_600
            item["metrics"]["local_admission_rejected"] = 21_599
            item["metrics"]["actual_offered_rps"] = 720.0
    with pytest.raises(X1ResumeTestbedError, match="matched_load_median_tolerance"):
        validate_evidence(payload, config())


def test_private_validator_recomputes_repository_q0_and_attempt_digests(tmp_path: Path) -> None:
    payload = complete_payload()
    suite_root = tmp_path / "suite"
    repository_root = tmp_path / "repository"
    source_root = tmp_path / "source"
    suite_root.mkdir()
    repository_root.mkdir()
    source_root.mkdir()

    entries = []
    model_identities = {}
    for profile in ("off", "on"):
        for model_id in EXPECTED_MODELS:
            artifact = repository_root / f"batch-{profile}/{model_id}/1/model.pt"
            model_config = repository_root / f"batch-{profile}/{model_id}/config.pbtxt"
            artifact.parent.mkdir(parents=True)
            artifact.write_bytes(f"artifact:{profile}:{model_id}".encode())
            model_config.write_text("instance_group { kind: KIND_GPU }\n", encoding="utf-8")
            for path in (artifact, model_config):
                entries.append(
                    {
                        "path": path.relative_to(repository_root).as_posix(),
                        "bytes": path.stat().st_size,
                        "sha256": sha256_file(path),
                    }
                )
            model_identities[f"{profile}:{model_id}"] = {
                "artifact_sha256": sha256_file(artifact),
                "config_sha256": sha256_file(model_config),
            }
    entries.sort(key=lambda item: item["path"])
    profile_identities = {
        profile: {
            "entry_count": len(
                selected := [
                    item for item in entries if item["path"].startswith(f"batch-{profile}/")
                ]
            ),
            "repository_sha256": canonical_sha256(selected),
        }
        for profile in ("off", "on")
    }
    manifest = {
        "entries": entries,
        "repository_sha256": canonical_sha256(entries),
        "profile_identities": profile_identities,
        "model_identities": model_identities,
        "source_blobs": [],
    }
    manifest_path = repository_root / "model-repository-manifest.json"
    canonical_write(manifest_path, manifest)
    payload["source_blobs"] = []
    payload["environment"] = {
        "repository_manifest_sha256": sha256_file(manifest_path),
        "repository_sha256": manifest["repository_sha256"],
    }

    q0_root = suite_root / "q0-isolated"
    q0_root.mkdir()
    trace_values = []
    log_lines = []
    for index, item in enumerate(payload["q0"], start=1):
        model_id = item["model_id"]
        item.update(model_identities[f"off:{model_id}"])
        before = (
            f'nv_inference_request_success{{model="{model_id}"}} 0\n'
            f'nv_inference_compute_infer_duration_us{{model="{model_id}"}} 0\n'
        )
        after = (
            f'nv_inference_request_success{{model="{model_id}"}} 1\n'
            f'nv_inference_compute_infer_duration_us{{model="{model_id}"}} 1\n'
        )
        log_line = f"{model_id} GPU device 0"
        log_lines.append(log_line)
        raw_path = q0_root / f"q0-{model_id}.json"
        canonical_write(
            raw_path,
            {
                "model_id": model_id,
                "metrics_before": before,
                "metrics_after": after,
                "gpu_samples": [{"utilization_percent": 1}],
                "gpu_log_lines": [log_line],
                "isolated_request_count": 64,
            },
        )
        item.update(
            {
                "triton_success_delta": 1.0,
                "metrics_before_sha256": canonical_sha256(before),
                "metrics_after_sha256": canonical_sha256(after),
                "isolated_gpu_sample_count": 1,
                "gpu_log_line_sha256": [canonical_sha256(log_line)],
                "private_raw": {
                    "path": raw_path.relative_to(suite_root).as_posix(),
                    "bytes": raw_path.stat().st_size,
                    "sha256": sha256_file(raw_path),
                },
            }
        )
        trace_values.extend(
            [
                {"id": index, "model_name": model_id},
                {"id": index, "timestamps": [{"name": "COMPUTE_START"}]},
            ]
        )
    log_path = q0_root / "triton.log"
    log_path.write_text("\n".join(log_lines) + "\n", encoding="utf-8")
    trace_path = q0_root / "triton-trace.json"
    canonical_write(trace_path, trace_values)
    payload["profile_evidence"] = {
        "q0_isolated": {
            "trace": {
                "path": trace_path.relative_to(suite_root).as_posix(),
                "bytes": trace_path.stat().st_size,
                "sha256": sha256_file(trace_path),
            },
            "log": {
                "path": log_path.relative_to(suite_root).as_posix(),
                "bytes": log_path.stat().st_size,
                "sha256": sha256_file(log_path),
            },
            "compute_start_counts": {model_id: 1 for model_id in EXPECTED_MODELS},
        }
    }
    for profile in ("off", "on"):
        profile_root = suite_root / f"batch-{profile}"
        profile_root.mkdir()
        profile_log = profile_root / "triton.log"
        profile_log.write_text(f"batch-{profile}\n", encoding="utf-8")
        payload["profile_evidence"][profile] = {
            "log": {
                "path": profile_log.relative_to(suite_root).as_posix(),
                "bytes": profile_log.stat().st_size,
                "sha256": sha256_file(profile_log),
            }
        }
    attempts_root = suite_root / "attempts"
    attempts_root.mkdir()
    for item in payload["runs"]:
        active_models = {
            model_id for model_id, fraction in item["model_mix"].items() if fraction > 0
        }
        formed_batch = item["cell_id"] == "balanced-concurrent-batch-on"
        before_lines = []
        after_lines = []
        deltas = {}
        for model_id in EXPECTED_MODELS:
            inference_count = (
                2.0
                if formed_batch and model_id in active_models
                else float(model_id in active_models)
            )
            execution_count = float(model_id in active_models)
            values = {
                "success": float(model_id in active_models),
                "compute_us": float(model_id in active_models),
                "inference_count": inference_count,
                "execution_count": execution_count,
            }
            metric_names = {
                "success": "nv_inference_request_success",
                "compute_us": "nv_inference_compute_infer_duration_us",
                "inference_count": "nv_inference_count",
                "execution_count": "nv_inference_exec_count",
            }
            for field, metric_name in metric_names.items():
                before_lines.append(f'{metric_name}{{model="{model_id}"}} 0')
                after_lines.append(f'{metric_name}{{model="{model_id}"}} {values[field]}')
            deltas[model_id] = values
        metrics_before = "\n".join(before_lines) + "\n"
        metrics_after = "\n".join(after_lines) + "\n"
        item["triton_metric_deltas"] = deltas
        raw_path = attempts_root / f"{item['attempt_id']}.json"
        canonical_write(
            raw_path,
            {
                "attempt_id": item["attempt_id"],
                "cell": {
                    "cell_id": item["cell_id"],
                    "model_mix": item["model_mix"],
                },
                "repetition": item["repetition"],
                "records": synthetic_records(item["model_mix"]),
                "measurement_window": {"start_ns": 0, "end_ns": 1_000, "seconds": 30},
                "admission": {
                    "offered": 24_000,
                    "admitted": len(active_models),
                    "local_admission_rejected": 24_000 - len(active_models),
                },
                "drain_seconds": 0.1,
                "metrics": item["metrics"],
                "triton_metric_deltas": deltas,
                "cross_model_request_overlap": item["cross_model_request_overlap"],
                "batching_proof": item["batching_proof"],
                "metrics_before": metrics_before,
                "metrics_after": metrics_after,
            },
        )
        item["private_raw"] = {
            "path": raw_path.relative_to(suite_root).as_posix(),
            "bytes": raw_path.stat().st_size,
            "sha256": sha256_file(raw_path),
        }

    prometheus_snapshot = {
        "jobs": list(EXPECTED_PROMETHEUS_JOBS),
        "total": 5,
        "up": 5,
    }
    prometheus_sample = {
        "observed_at": "2026-08-25T00:00:00Z",
        "probe_budget_seconds": config().cleanup_timeout_seconds,
        "probe_finished_elapsed_seconds": 0.1,
        "probe_started_elapsed_seconds": 0.0,
        "snapshot": prometheus_snapshot,
        "state": "ready",
    }
    final_checks = {
        "holder": {"uid": "synthetic"},
        "prometheus": prometheus_snapshot,
        "prometheus_restore_ready": True,
        "prometheus_restore_samples": [prometheus_sample],
        "prometheus_restore_seconds": 0.1,
        "prometheus_restore_terminal_reason": "ready",
        "queues": {"active": 0},
    }
    cleanup_path = suite_root / "cleanup.json"
    canonical_write(
        cleanup_path,
        {"cleanup": payload["cleanup"], "final_checks": final_checks},
    )
    payload["cleanup_evidence"] = {
        "path": cleanup_path.relative_to(suite_root).as_posix(),
        "bytes": cleanup_path.stat().st_size,
        "sha256": sha256_file(cleanup_path),
        "final_checks_sha256": canonical_sha256(final_checks),
    }

    result = validate_evidence(
        payload,
        config(),
        private_suite_root=suite_root,
        model_repository_root=repository_root,
        source_root=source_root,
    )
    assert result["private_artifacts_valid"] is True
    report = generate_report(
        payload,
        config(),
        private_suite_root=suite_root,
        model_repository_root=repository_root,
        source_root=source_root,
    )
    bullet = report["resume_bullets"][0]
    assert "preliminary" in bullet
    assert "API replica" not in bullet
    assert "kernel overlap" not in bullet
    assert "n=3 median" in bullet
    assert "no training-quality or model-accuracy claim" in bullet
    assert report["measured"]["topology_comparison_scope"].startswith("compound client-driver")
    public_attempt = payload["runs"][0]
    tampered = attempts_root / f"{public_attempt['attempt_id']}.json"
    original_raw = json.loads(tampered.read_bytes())

    def rewrite_attempt(raw_payload: dict[str, object]) -> None:
        canonical_write(tampered, raw_payload)
        public_attempt["private_raw"].update(
            {"bytes": tampered.stat().st_size, "sha256": sha256_file(tampered)}
        )

    records_tampered = json.loads(json.dumps(original_raw))
    records_tampered["records"] = []
    rewrite_attempt(records_tampered)
    with pytest.raises(X1ResumeTestbedError, match="private_attempt"):
        validate_evidence(
            payload,
            config(),
            private_suite_root=suite_root,
            model_repository_root=repository_root,
            source_root=source_root,
        )
    summary_tampered = json.loads(json.dumps(original_raw))
    summary_tampered["metrics"]["throughput_rps"] = 999.0
    rewrite_attempt(summary_tampered)
    with pytest.raises(X1ResumeTestbedError, match="private_attempt"):
        validate_evidence(
            payload,
            config(),
            private_suite_root=suite_root,
            model_repository_root=repository_root,
            source_root=source_root,
        )
    deltas_tampered = json.loads(json.dumps(original_raw))
    deltas_tampered["triton_metric_deltas"][EXPECTED_MODELS[0]]["success"] = 9.0
    rewrite_attempt(deltas_tampered)
    with pytest.raises(X1ResumeTestbedError, match="private_attempt"):
        validate_evidence(
            payload,
            config(),
            private_suite_root=suite_root,
            model_repository_root=repository_root,
            source_root=source_root,
        )
    rewrite_attempt(original_raw)
    original_cleanup = json.loads(cleanup_path.read_bytes())

    def rewrite_cleanup(raw_payload: dict[str, object]) -> None:
        canonical_write(cleanup_path, raw_payload)
        payload["cleanup_evidence"].update(
            {
                "bytes": cleanup_path.stat().st_size,
                "sha256": sha256_file(cleanup_path),
                "final_checks_sha256": canonical_sha256(raw_payload["final_checks"]),
            }
        )

    terminal_not_ready = json.loads(json.dumps(original_cleanup))
    terminal_not_ready["final_checks"]["prometheus"] = {
        **prometheus_snapshot,
        "up": 4,
    }
    terminal_not_ready["final_checks"]["prometheus_restore_ready"] = False
    terminal_not_ready["final_checks"]["prometheus_restore_terminal_reason"] = "timeout"
    terminal_not_ready["final_checks"]["prometheus_restore_samples"][-1].update(
        {
            "snapshot": {**prometheus_snapshot, "up": 4},
            "state": "retryable_4_of_5",
        }
    )
    rewrite_cleanup(terminal_not_ready)
    with pytest.raises(X1ResumeTestbedError, match="private_prometheus_cleanup"):
        validate_evidence(
            payload,
            config(),
            private_suite_root=suite_root,
            model_repository_root=repository_root,
            source_root=source_root,
        )

    elapsed_over_timeout = json.loads(json.dumps(original_cleanup))
    elapsed_over_timeout["final_checks"]["prometheus_restore_seconds"] = 121.0
    elapsed_over_timeout["final_checks"]["prometheus_restore_samples"][-1][
        "probe_finished_elapsed_seconds"
    ] = 121.0
    rewrite_cleanup(elapsed_over_timeout)
    with pytest.raises(X1ResumeTestbedError, match="private_prometheus_cleanup"):
        validate_evidence(
            payload,
            config(),
            private_suite_root=suite_root,
            model_repository_root=repository_root,
            source_root=source_root,
        )

    illegal_transition = json.loads(json.dumps(original_cleanup))
    wrong_snapshot = {
        "jobs": [*EXPECTED_PROMETHEUS_JOBS[:-1], "wrong-job"],
        "total": 5,
        "up": 5,
    }
    illegal_transition["final_checks"]["prometheus_restore_samples"] = [
        {
            "observed_at": "2026-08-25T00:00:00Z",
            "probe_budget_seconds": config().cleanup_timeout_seconds,
            "probe_finished_elapsed_seconds": 0.05,
            "probe_started_elapsed_seconds": 0.0,
            "snapshot": wrong_snapshot,
            "state": "invalid_snapshot",
        },
        prometheus_sample,
    ]
    rewrite_cleanup(illegal_transition)
    with pytest.raises(X1ResumeTestbedError, match="private_prometheus_cleanup"):
        validate_evidence(
            payload,
            config(),
            private_suite_root=suite_root,
            model_repository_root=repository_root,
            source_root=source_root,
        )

    rewrite_cleanup(original_cleanup)
    cleanup_tampered = json.loads(json.dumps(original_cleanup))
    cleanup_tampered["cleanup"]["container_absent"] = False
    rewrite_cleanup(cleanup_tampered)
    with pytest.raises(X1ResumeTestbedError, match="private_cleanup"):
        validate_evidence(
            payload,
            config(),
            private_suite_root=suite_root,
            model_repository_root=repository_root,
            source_root=source_root,
        )


def test_batching_and_cross_model_overlap_fail_closed() -> None:
    payload = complete_payload()
    batch_on = next(
        item for item in payload["runs"] if item["cell_id"] == "balanced-concurrent-batch-on"
    )
    batch_on["batching_proof"] = {
        "formed_batch_observed": False,
        "formed_mean_batch_size": 1.0,
    }
    batch_on["cross_model_request_overlap"]["observed"] = False
    with pytest.raises(X1ResumeTestbedError, match="batch_not_formed|cross_model_request_overlap"):
        validate_evidence(payload, config())


def test_distinct_x1_resume_gpu_lease_identity(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("EVM_SCENARIO_GPU_LEASE_ROOT", str(tmp_path / "lease"))
    lease = acquire_scale_validation_gpu_lease(
        "x1-resume-unit-test",
        source_commit="a" * 40,
        purpose="scale_validation_inference",
        scenario_id="X1-RESUME",
        model_family="tabular",
    )
    assert lease.scenario_id == "X1-RESUME"
    release_scale_validation_gpu_lease(
        run_id=lease.run_id,
        lease_id=lease.lease_id,
        fencing_token=lease.fencing_token,
        reason="test complete",
    )
    with pytest.raises(ScenarioWorkloadError) as rejected:
        acquire_scale_validation_gpu_lease(
            "s8-v4-e0-spoofed-x1",
            source_commit="a" * 40,
            purpose="scale_validation_inference",
            scenario_id="X1-RESUME",
            model_family="tabular",
        )
    assert rejected.value.code == "scale_validation_gpu_lease_identity_invalid"
