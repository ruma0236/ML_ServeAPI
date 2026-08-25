from __future__ import annotations

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
    X1ResumeConfig,
    X1ResumeTestbedError,
    canonical_sha256,
    canonical_write,
    deterministic_model_schedule,
    generate_report,
    jain_fairness,
    sha256_file,
    summarize_requests,
    triton_trace_compute_counts,
    validate_evidence,
)

# Importing the host runner is intentionally avoided; assert the frozen Triton
# 25.08 token form directly from its committed source.


ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "configs/s8_v4_x1_resume_testbed_v1.toml"


def config() -> X1ResumeConfig:
    return X1ResumeConfig.from_path(CONFIG)


def metric_payload(model_mix: dict[str, float] | None = None) -> dict[str, object]:
    model_mix = model_mix or {model: 0.25 for model in EXPECTED_MODELS}
    per_model = {
        model: {
            "window_completed": 1,
            "admitted_cohort_completed": 1,
            "throughput_rps": 1 / 30,
            "p99_ms": 1.0,
        }
        for model in EXPECTED_MODELS
    }
    return {
        "offered": 4,
        "admitted": 4,
        "local_admission_rejected": 0,
        "window_completed": 4,
        "window_http_5xx": 0,
        "window_other_errors": 0,
        "admitted_cohort_completed": 4,
        "admitted_cohort_http_5xx": 0,
        "admitted_cohort_other_errors": 0,
        "tail_completed": 0,
        "loss": 0,
        "duplicates": 0,
        "throughput_rps": 4 / 30,
        "actual_offered_rps": 4 / 30,
        "drain_seconds": 0.1,
        "latency_ms": {"p50": 1.0, "p95": 1.0, "p99": 1.0},
        "queue_wait_ms": {"p50": 0.1, "p95": 0.1, "p99": 0.1},
        "per_model": per_model,
        "fairness_target_basis": "model_mix * actual_window_offered_rps",
        "raw_throughput_jain_fairness": 1.0,
        "normalized_attainment_jain_fairness": jain_fairness(
            [
                (1 / 30) / (fraction * (4 / 30))
                for model, fraction in model_mix.items()
                if model in EXPECTED_MODELS and fraction > 0
            ]
        ),
    }


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
                        "warmup_seconds": cfg.warmup_seconds,
                        "measurement_seconds": cfg.measurement_seconds,
                    },
                    "metrics": metric_payload(dict(cell.model_mix)),
                    "triton_execution_proved": True,
                    "cpu_fallback_observed": False,
                    "cross_model_request_overlap_required": cell.client_workers > 1
                    and len(cell.model_mix) > 1,
                    "cross_model_request_overlap": {
                        "observed": cell.client_workers > 1 and len(cell.model_mix) > 1,
                        "distinct_model_pairs": [["criteo_dlrm_lite", "higgs_tiny_mlp"]],
                    },
                    "batching_proof": {
                        "formed_batch_observed": cell.cell_id == "balanced-concurrent-batch-on",
                        "formed_mean_batch_size": 2.0
                        if cell.cell_id == "balanced-concurrent-batch-on"
                        else 1.0,
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
        item["triton_metric_deltas"] = {}
        raw_path = attempts_root / f"{item['attempt_id']}.json"
        canonical_write(
            raw_path,
            {
                "attempt_id": item["attempt_id"],
                "metrics": item["metrics"],
                "triton_metric_deltas": {},
                "cross_model_request_overlap": item["cross_model_request_overlap"],
                "batching_proof": item["batching_proof"],
            },
        )
        item["private_raw"] = {
            "path": raw_path.relative_to(suite_root).as_posix(),
            "bytes": raw_path.stat().st_size,
            "sha256": sha256_file(raw_path),
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
    tampered = attempts_root / f"{payload['runs'][0]['attempt_id']}.json"
    tampered.write_text("{}\n", encoding="utf-8")
    with pytest.raises(X1ResumeTestbedError, match="private_digest"):
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
