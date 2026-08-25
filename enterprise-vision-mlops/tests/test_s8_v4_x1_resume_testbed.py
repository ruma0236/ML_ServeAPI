from __future__ import annotations

import json
import hashlib
import os
import runpy
import subprocess
from collections import Counter
from itertools import groupby
from pathlib import Path

import pytest

import evm.scale_validation.x1_resume_testbed as x1_resume_module

from evm.control_panel.scenario_workloads import (
    ScenarioWorkloadError,
    acquire_scale_validation_gpu_lease,
    release_scale_validation_gpu_lease,
)
from evm.scale_validation.x1_resume_testbed import (
    DEFAULT_CONFIG_RELATIVE_PATH,
    EXPECTED_MODELS,
    EXPECTED_PROMETHEUS_JOBS,
    REQUIRED_SOURCE_BLOB_PATHS,
    X1ResumeConfig,
    X1ResumeTestbedError,
    _bound_file,
    canonical,
    canonical_sha256,
    canonical_write,
    deterministic_model_schedule,
    generate_report,
    prometheus_baseline_ready,
    require_default_config_path,
    request_interval_overlap,
    sha256_file,
    summarize_requests,
    triton_gpu_instance_exact,
    triton_trace_compute_counts,
    validate_evidence,
    validate_gpu_samples,
    validate_report_binding,
    wait_for_prometheus_baseline,
)

# Importing the host runner is intentionally avoided; assert the frozen Triton
# 25.08 token form directly from its committed source.


ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "configs/s8_v4_x1_resume_testbed_v1.toml"


def config() -> X1ResumeConfig:
    return X1ResumeConfig.from_path(CONFIG)


def governed_fixture(
    root: Path, cfg: X1ResumeConfig, monkeypatch: pytest.MonkeyPatch
) -> tuple[Path, dict[str, object]]:
    data_root = root / "data"
    dataset_identity = "1" * 64
    split_identity = "2" * 64

    def write(relative: str, payload: object | bytes) -> Path:
        path = data_root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(payload, bytes):
            path.write_bytes(payload)
        else:
            canonical_write(path, payload)
        return path

    logistic_relative = "artifacts/scale_validation/s3/higgs-fixture/models/logistic.json"
    probabilistic_relative = "artifacts/scale_validation/s3/higgs-fixture/models/probabilistic.json"
    logistic = write(
        logistic_relative,
        {
            "schema_version": "evm.s3_capacity_probe_artifact.v1",
            "dataset_identity_sha256": dataset_identity,
        },
    )
    probabilistic = write(
        probabilistic_relative,
        {
            "schema_version": "evm.s3_capacity_probe_artifact.v1",
            "dataset_identity_sha256": dataset_identity,
        },
    )
    replay_relative = "artifacts/scale_validation/s3/higgs-fixture/replay/features.npy"
    replay = write(replay_relative, b"fixture-replay-bytes")
    s3_registry_relative = "artifacts/scale_validation/s3/capacity-registry.json"
    s3_registry = write(
        s3_registry_relative,
        {
            "schema_version": "evm.s3_capacity_registry.v1",
            "dataset_identity_sha256": dataset_identity,
            "split_manifest_sha256": split_identity,
            "probes": {
                "logistic": {
                    "artifact_uri": logistic_relative.removeprefix(
                        "artifacts/scale_validation/s3/"
                    ),
                    "artifact_sha256": sha256_file(logistic),
                },
                "probabilistic": {
                    "artifact_uri": probabilistic_relative.removeprefix(
                        "artifacts/scale_validation/s3/"
                    ),
                    "artifact_sha256": sha256_file(probabilistic),
                },
            },
        },
    )
    s4_artifact_relative = "artifacts/scale_validation/s4/tiny-mlp-v1/tiny-mlp.pt"
    s4_artifact = write(s4_artifact_relative, b"fixture-tiny-mlp")
    s4_registry_relative = "artifacts/scale_validation/s4/tiny-mlp-v1/registry.json"
    s4_registry = write(
        s4_registry_relative,
        {
            "schema_version": "evm.s4_gpu_batch_registry.v1",
            "artifact_sha256": sha256_file(s4_artifact),
            "model_identity_sha256": "3" * 64,
            "preprocessing_sha256": "4" * 64,
            "dataset_identity_sha256": dataset_identity,
        },
    )
    s5_shard_relative = "datasets/criteo-click-logs/s5/governed/shard-000.parquet"
    s5_shard = write(s5_shard_relative, b"fixture-parquet")
    s5_manifest_relative = "datasets/criteo-click-logs/s5/governed/dataset-manifest.json"
    s5_manifest = write(
        s5_manifest_relative,
        {
            "schema_version": "evm.s5_criteo_dataset_manifest.v1",
            "dataset_version": "criteo-fixture-v1",
            "source_revision": "5" * 40,
            "shards": [
                {
                    "governed_path": "shard-000.parquet",
                    "governed_sha256": sha256_file(s5_shard),
                }
            ],
        },
    )
    identities = {
        "s3_registry": {
            "path": s3_registry_relative,
            "sha256": sha256_file(s3_registry),
            "bytes": s3_registry.stat().st_size,
            "schema_version": "evm.s3_capacity_registry.v1",
            "dataset_identity_sha256": dataset_identity,
            "split_manifest_sha256": split_identity,
        },
        "s3_replay": {
            "path": replay_relative,
            "sha256": sha256_file(replay),
            "bytes": replay.stat().st_size,
        },
        "s3_logistic": {
            "path": logistic_relative,
            "sha256": sha256_file(logistic),
            "bytes": logistic.stat().st_size,
            "schema_version": "evm.s3_capacity_probe_artifact.v1",
        },
        "s3_probabilistic": {
            "path": probabilistic_relative,
            "sha256": sha256_file(probabilistic),
            "bytes": probabilistic.stat().st_size,
            "schema_version": "evm.s3_capacity_probe_artifact.v1",
        },
        "s4_registry": {
            "path": s4_registry_relative,
            "sha256": sha256_file(s4_registry),
            "bytes": s4_registry.stat().st_size,
            "schema_version": "evm.s4_gpu_batch_registry.v1",
            "model_identity_sha256": "3" * 64,
            "preprocessing_sha256": "4" * 64,
        },
        "s4_artifact": {
            "path": s4_artifact_relative,
            "sha256": sha256_file(s4_artifact),
            "bytes": s4_artifact.stat().st_size,
        },
        "s5_manifest": {
            "path": s5_manifest_relative,
            "sha256": sha256_file(s5_manifest),
            "bytes": s5_manifest.stat().st_size,
            "schema_version": "evm.s5_criteo_dataset_manifest.v1",
            "dataset_version": "criteo-fixture-v1",
            "source_revision": "5" * 40,
            "first_shard_path": "shard-000.parquet",
            "first_shard_sha256": sha256_file(s5_shard),
            "first_shard_bytes": s5_shard.stat().st_size,
        },
    }
    monkeypatch.setattr(x1_resume_module, "GOVERNED_SOURCE_IDENTITIES", identities)
    replay_binding = {
        "registry_path": s3_registry_relative,
        "registry_sha256": sha256_file(s3_registry),
        "registry_bytes": s3_registry.stat().st_size,
        "replay_path": replay_relative,
        "replay_sha256": sha256_file(replay),
        "replay_bytes": replay.stat().st_size,
        "replay_shape": [100000, 28],
        "sample_shape": [cfg.sample_rows_per_dataset, 28],
        "dataset_identity_sha256": dataset_identity,
        "split_manifest_sha256": split_identity,
    }
    bindings = {
        "higgs_logistic_regression": {
            "source_schema": "evm.s3_capacity_probe_artifact.v1",
            "source_path": logistic_relative,
            "source_sha256": sha256_file(logistic),
            "source_bytes": logistic.stat().st_size,
            "dataset_identity_sha256": dataset_identity,
            "replay": replay_binding,
        },
        "higgs_gaussian_nb": {
            "source_schema": "evm.s3_capacity_probe_artifact.v1",
            "source_path": probabilistic_relative,
            "source_sha256": sha256_file(probabilistic),
            "source_bytes": probabilistic.stat().st_size,
            "dataset_identity_sha256": dataset_identity,
            "replay": replay_binding,
        },
        "higgs_tiny_mlp": {
            "source_schema": "evm.s4_gpu_batch_registry.v1",
            "source_path": s4_artifact_relative,
            "source_sha256": sha256_file(s4_artifact),
            "source_bytes": s4_artifact.stat().st_size,
            "model_identity_sha256": "3" * 64,
            "registry_sha256": sha256_file(s4_registry),
            "registry_path": s4_registry_relative,
            "registry_bytes": s4_registry.stat().st_size,
            "preprocessing_sha256": "4" * 64,
            "dataset_identity_sha256": dataset_identity,
            "split_manifest_sha256": split_identity,
            "replay": replay_binding,
        },
        "criteo_dlrm_lite": {
            "manifest_path": s5_manifest_relative,
            "manifest_sha256": sha256_file(s5_manifest),
            "manifest_bytes": s5_manifest.stat().st_size,
            "dataset_version": "criteo-fixture-v1",
            "source_revision": "5" * 40,
            "shard_path": "shard-000.parquet",
            "shard_sha256": sha256_file(s5_shard),
            "shard_bytes": s5_shard.stat().st_size,
            "sample_rows": cfg.sample_rows_per_dataset,
            "categorical_hash": "sha256-first-u64-mod-4096",
            "dense_transform": "log1p(max(value,0))",
            "parameter_origin": "deterministic_seeded_testbed_initialization",
            "training_or_quality_claim": False,
            "seed": cfg.seed,
        },
    }
    return data_root, bindings


def triton_config_readback(model_id: str, cfg: X1ResumeConfig) -> dict[str, object]:
    input_width = next(model.input_width for model in cfg.models if model.model_id == model_id)
    return {
        "name": model_id,
        "backend": "pytorch",
        "max_batch_size": "32",
        "input": [{"name": "FEATURES__0", "data_type": "TYPE_FP32", "dims": [str(input_width)]}],
        "output": [{"name": "SCORE__0", "data_type": "TYPE_FP32", "dims": ["1"]}],
        "instance_group": [{"kind": "KIND_GPU", "count": "1", "gpus": ["0"]}],
    }


def synthetic_records(model_mix: dict[str, float]) -> list[dict[str, object]]:
    records = []
    sequence = 0
    measurement_start_ns = 1_000_000_000
    for model_id, fraction in model_mix.items():
        if fraction <= 0:
            continue
        for index in range(100):
            enqueued_ns = measurement_start_ns + sequence * 10_000
            started_ns = enqueued_ns + 100_000
            finished_ns = started_ns + 1_000_000
            records.append(
                {
                    "request_id": f"synthetic-{model_id}-{index}",
                    "model_id": model_id,
                    "worker_id": sequence % 8,
                    "outcome": "completed",
                    "status": 200,
                    "enqueued_ns": enqueued_ns,
                    "started_ns": started_ns,
                    "finished_ns": finished_ns,
                    "latency_ms": 1.0,
                    "queue_wait_ms": 0.1,
                }
            )
            sequence += 1
    return records


def synthetic_attempt_bundle(attempt_id: str, model_mix: dict[str, float]) -> dict[str, object]:
    schedule = deterministic_model_schedule(model_mix)
    measurement_start_ns = 20_000_000_000
    measurement_end_ns = 50_000_000_000
    admission_ledger: list[dict[str, object]] = []
    terminal_records: list[dict[str, object]] = []
    measured_records: list[dict[str, object]] = []
    warmup_count = 100
    measured_offered = 24_000
    measured_accepted = 400
    total = warmup_count + measured_offered
    for sequence in range(total):
        phase = "warmup" if sequence < warmup_count else "measured"
        measured_sequence = sequence - warmup_count
        enqueued_ns = (
            10_000_000_000 + sequence * 1_000_000
            if phase == "warmup"
            else measurement_start_ns + measured_sequence * 1_250_000
        )
        accepted = phase == "warmup" or measured_sequence < measured_accepted
        model_id = schedule[sequence % len(schedule)]
        request_id = f"{attempt_id}-{sequence}"
        admission_ledger.append(
            {
                "global_sequence": sequence,
                "request_id": request_id,
                "model_id": model_id,
                "phase": phase,
                "enqueued_ns": enqueued_ns,
                "decision_ns": enqueued_ns + 1_000,
                "decision": "accepted" if accepted else "rejected",
                "reason": "local_queue_capacity" if accepted else "local_queue_full",
            }
        )
        if not accepted:
            continue
        started_ns = enqueued_ns + 100_000
        finished_ns = started_ns + 2_000_000
        terminal = {
            "request_id": request_id,
            "model_id": model_id,
            "worker_id": sequence % 8,
            "outcome": "completed",
            "status": 200,
            "enqueued_ns": enqueued_ns,
            "started_ns": started_ns,
            "finished_ns": finished_ns,
            "latency_ms": 2.0,
            "queue_wait_ms": 0.1,
            "global_sequence": sequence,
            "phase": phase,
        }
        terminal_records.append(terminal)
        if phase == "measured":
            measured_records.append(
                {
                    key: value
                    for key, value in terminal.items()
                    if key not in {"global_sequence", "phase"}
                }
            )
    measured_ledger = [item for item in admission_ledger if item["phase"] == "measured"]
    admission_proof = {
        "issued_count": len(admission_ledger),
        "warmup_offered": warmup_count,
        "measured_offered": measured_offered,
        "measured_accepted": measured_accepted,
        "measured_rejected": measured_offered - measured_accepted,
        "measured_offered_by_model": {
            model_id: sum(item["model_id"] == model_id for item in measured_ledger)
            for model_id in EXPECTED_MODELS
        },
        "measured_accepted_by_model": {
            model_id: sum(
                item["model_id"] == model_id and item["decision"] == "accepted"
                for item in measured_ledger
            )
            for model_id in EXPECTED_MODELS
        },
        "measured_rejected_by_model": {
            model_id: sum(
                item["model_id"] == model_id and item["decision"] == "rejected"
                for item in measured_ledger
            )
            for model_id in EXPECTED_MODELS
        },
        "ledger_sha256": canonical_sha256(admission_ledger),
        "terminal_records_sha256": canonical_sha256(terminal_records),
    }
    return {
        "records": measured_records,
        "terminal_records": terminal_records,
        "admission_ledger": admission_ledger,
        "admission": {
            "offered": measured_offered,
            "admitted": measured_accepted,
            "local_admission_rejected": measured_offered - measured_accepted,
        },
        "admission_proof": admission_proof,
        "measurement_window": {
            "start_ns": measurement_start_ns,
            "end_ns": measurement_end_ns,
            "seconds": 30,
        },
    }


def metric_payload(model_mix: dict[str, float] | None = None) -> dict[str, object]:
    model_mix = model_mix or {model: 0.25 for model in EXPECTED_MODELS}
    records = synthetic_records(model_mix)
    return summarize_requests(
        offered=24_000,
        admitted=len(records),
        local_admission_rejected=24_000 - len(records),
        records=records,
        measurement_seconds=30,
        measurement_start_ns=1_000_000_000,
        measurement_end_ns=31_000_000_000,
        drain_seconds=0.1,
        model_mix=model_mix,
    )


def complete_payload() -> dict[str, object]:
    cfg = config()
    suite_id = "x1-resume-20260825T000000Z-aaaaaaaa"
    q0 = [
        {
            "model_id": model,
            "artifact_sha256": "a" * 64,
            "config_sha256": "b" * 64,
            "triton_config_readback": triton_config_readback(model, cfg),
            "cuda_activity_observed": True,
            "cpu_fallback_observed": False,
            "triton_gpu_instance_proof": True,
            "triton_success_delta": 64.0,
            "triton_compute_delta": 64.0,
            "triton_inference_count_delta": 2048.0,
            "triton_execution_count_delta": 64.0,
            "isolated_gpu_busy_samples": 1,
            "isolated_request_count": 64,
            "request_batch_size": 32,
            "triton_trace_compute_start_count": 1,
        }
        for model in EXPECTED_MODELS
    ]
    runs = []
    run_index = 0
    for cell in cfg.cells:
        for repetition in range(1, cell.repetitions + 1):
            run_index += 1
            records = synthetic_records(dict(cell.model_mix))
            active_count = len(records)
            formed_batch = cell.cell_id == "balanced-concurrent-batch-on"
            runs.append(
                {
                    "attempt_id": f"{suite_id}-{cell.cell_id}-r{repetition}-{run_index:08x}",
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
                        "inference_count_delta": float(active_count),
                        "execution_count_delta": float(
                            active_count / 2 if formed_batch else active_count
                        ),
                        "formed_batch_observed": formed_batch,
                        "formed_mean_batch_size": 2.0 if formed_batch else 1.0,
                    },
                }
            )
    return {
        "schema_version": "evm.s8_v4.x1_resume_testbed.v1",
        "suite_id": suite_id,
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
            "gpu_identity_restored": True,
            "gpu_vram_restored": True,
            "prometheus_5_of_5": True,
            "prometheus_exact_jobs_restored": True,
            "errors": [],
        },
        "cleanup_evidence": {
            "path": "cleanup.json",
            "bytes": 1,
            "sha256": "c" * 64,
            "final_checks_sha256": "d" * 64,
            "released_gpu_lease": {"sha256": "e" * 64},
            "released_gpu_lease_archive": {"sha256": "f" * 64},
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

    runner = (ROOT / "scripts/dev/run_s8_v4_x1_resume_testbed.py").read_text(encoding="utf-8")
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
        {"jobs": [*EXPECTED_PROMETHEUS_JOBS, "extra"], "total": 5, "up": 5},
        {
            "jobs": [*EXPECTED_PROMETHEUS_JOBS, EXPECTED_PROMETHEUS_JOBS[0]],
            "total": 5,
            "up": 5,
        },
    )
    for snapshot in invalid:
        with pytest.raises(X1ResumeTestbedError, match="x1_resume_prometheus_preflight"):
            assert_preflight(snapshot)


def test_default_config_path_and_frozen_matrix_fail_closed(tmp_path: Path) -> None:
    assert require_default_config_path(CONFIG, ROOT) == CONFIG.resolve()
    alternate = tmp_path / CONFIG.name
    alternate.write_bytes(CONFIG.read_bytes())
    with pytest.raises(X1ResumeTestbedError, match="x1_resume_default_config_required"):
        require_default_config_path(alternate, ROOT)

    mutated = CONFIG.read_text(encoding="utf-8").replace(
        "offered_requests_per_second = 800", "offered_requests_per_second = 801"
    )
    mutated_path = tmp_path / "mutated.toml"
    mutated_path.write_text(mutated, encoding="utf-8", newline="\n")
    with pytest.raises(X1ResumeTestbedError, match="x1_resume_config_digest"):
        X1ResumeConfig.from_path(mutated_path)

    matrix_mutated = CONFIG.read_text(encoding="utf-8").replace(
        'cell_id = "balanced-serial"', 'cell_id = "balanced-serial-mutated"', 1
    )
    matrix_path = tmp_path / "matrix-mutated.toml"
    matrix_path.write_text(matrix_mutated, encoding="utf-8", newline="\n")
    with pytest.raises(X1ResumeTestbedError, match="x1_resume_config_digest"):
        X1ResumeConfig.from_path(matrix_path)


def test_private_and_s5_manifest_paths_reject_symlink_escape(tmp_path: Path) -> None:
    root = tmp_path / "root"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    escaped = outside / "escaped.json"
    escaped.write_text("{}\n", encoding="utf-8")
    link = root / "linked"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError:
        if os.name != "nt":
            raise
        subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(outside)],
            check=True,
            capture_output=True,
        )

    identity = {
        "path": "linked/escaped.json",
        "bytes": escaped.stat().st_size,
        "sha256": sha256_file(escaped),
    }
    with pytest.raises(X1ResumeTestbedError, match="x1_resume_private_containment"):
        _bound_file(root, identity, "symlink")

    prepare = runpy.run_path(
        str(ROOT / "scripts/dev/prepare_s8_v4_x1_resume_testbed.py"),
        run_name="x1_resume_prepare_path_test",
    )
    governed_manifest_file = prepare["governed_manifest_file"]
    manifest = root / "dataset-manifest.json"
    manifest.write_text("{}\n", encoding="utf-8")
    for raw_path in ("../outside/escaped.json", str(escaped.resolve())):
        with pytest.raises(X1ResumeTestbedError, match="x1_resume_s5_shard_path"):
            governed_manifest_file(manifest, raw_path, "test")
    with pytest.raises(X1ResumeTestbedError, match="x1_resume_s5_shard_containment"):
        governed_manifest_file(manifest, "linked/escaped.json", "test")


@pytest.mark.parametrize(
    ("field", "value", "reason"),
    [
        ("utilization_percent", float("nan"), "numeric"),
        ("utilization_percent", float("inf"), "numeric"),
        ("utilization_percent", "10", "numeric"),
        ("utilization_percent", True, "numeric"),
        ("utilization_percent", -1.0, "range"),
        ("utilization_percent", 100.1, "range"),
        ("memory_used_mib", -1.0, "range"),
        ("memory_used_mib", 20_000.0, "range"),
        ("memory_total_mib", float("inf"), "numeric"),
        ("memory_total_mib", 0.0, "range"),
    ],
)
def test_gpu_sample_semantics_fail_closed(field: str, value: object, reason: str) -> None:
    cfg = config()
    valid = {
        "uuid": cfg.expected_gpu_uuid,
        "name": cfg.expected_gpu_name,
        "memory_used_mib": 100.0,
        "memory_total_mib": 16_384.0,
        "utilization_percent": 10.0,
    }
    assert validate_gpu_samples([valid], cfg, label="positive") == {
        "sample_count": 1,
        "busy_sample_count": 1,
        "utilization_max_percent": 10.0,
        "vram_max_mib": 100.0,
    }
    mutated = dict(valid)
    mutated[field] = value
    with pytest.raises(X1ResumeTestbedError, match=f"x1_resume_gpu_sample_{reason}"):
        validate_gpu_samples([valid, mutated], cfg, label="mutation")


def test_gpu_sample_schema_and_identity_fail_closed() -> None:
    cfg = config()
    valid = {
        "uuid": cfg.expected_gpu_uuid,
        "name": cfg.expected_gpu_name,
        "memory_used_mib": 100.0,
        "memory_total_mib": 16_384.0,
        "utilization_percent": 10.0,
    }
    with pytest.raises(X1ResumeTestbedError, match="x1_resume_gpu_sample_schema"):
        validate_gpu_samples([valid, {"error": "nvidia-smi failed"}], cfg, label="error")
    wrong_identity = dict(valid)
    wrong_identity["uuid"] = "GPU-wrong"
    with pytest.raises(X1ResumeTestbedError, match="x1_resume_gpu_sample_identity"):
        validate_gpu_samples([wrong_identity], cfg, label="identity")


def test_triton_model_config_readback_is_exact_gpu_only() -> None:
    cfg = config()
    model = cfg.models[0]
    positive = triton_config_readback(model.model_id, cfg)
    assert triton_gpu_instance_exact(
        positive,
        model_id=model.model_id,
        input_width=model.input_width,
        dynamic_batching_enabled=False,
    )

    mutations: list[dict[str, object]] = []
    for invalid_count in (0, 8, True):
        mutated = json.loads(json.dumps(positive))
        mutated["instance_group"][0]["count"] = invalid_count
        mutations.append(mutated)
    missing_count = json.loads(json.dumps(positive))
    missing_count["instance_group"][0].pop("count")
    mutations.append(missing_count)
    empty_gpus = json.loads(json.dumps(positive))
    empty_gpus["instance_group"][0]["gpus"] = []
    mutations.append(empty_gpus)
    cpu = json.loads(json.dumps(positive))
    cpu["instance_group"][0]["kind"] = "KIND_CPU"
    mutations.append(cpu)
    mixed = json.loads(json.dumps(positive))
    mixed["instance_group"].append({"kind": "KIND_CPU", "count": "1", "gpus": ["0"]})
    mutations.append(mixed)
    for field, value in (("backend", "python"), ("max_batch_size", "8")):
        mutated = json.loads(json.dumps(positive))
        mutated[field] = value
        mutations.append(mutated)
    for collection, field, value in (
        ("input", "name", "WRONG"),
        ("input", "data_type", "TYPE_FP16"),
        ("input", "dims", ["99"]),
        ("output", "name", "WRONG"),
        ("output", "data_type", "TYPE_FP16"),
        ("output", "dims", ["2"]),
    ):
        mutated = json.loads(json.dumps(positive))
        mutated[collection][0][field] = value
        mutations.append(mutated)

    assert len(mutations) == 15
    assert all(
        not triton_gpu_instance_exact(
            mutated,
            model_id=model.model_id,
            input_width=model.input_width,
            dynamic_batching_enabled=False,
        )
        for mutated in mutations
    )


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
        measurement_start_ns=0,
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


def test_private_validator_recomputes_repository_q0_and_attempt_digests(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = complete_payload()
    suite_root = tmp_path / "suite"
    repository_root = tmp_path / "repository"
    source_root = tmp_path / "source"
    suite_root.mkdir()
    repository_root.mkdir()
    source_root.mkdir()

    for relative in REQUIRED_SOURCE_BLOB_PATHS:
        path = source_root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(
            CONFIG.read_bytes()
            if relative == DEFAULT_CONFIG_RELATIVE_PATH
            else f"fixture:{relative}\n".encode("utf-8")
        )
    replacement_path = source_root / "replacement-source.py"
    replacement_path.write_text("replacement = True\n", encoding="utf-8", newline="\n")

    def source_git(*args: str, binary: bool = False) -> str | bytes:
        completed = subprocess.run(
            ["git", *args],
            cwd=source_root,
            check=True,
            capture_output=True,
            text=not binary,
        )
        return completed.stdout if binary else completed.stdout.strip()

    source_git("init")
    source_git("config", "user.email", "x1-fixture@example.invalid")
    source_git("config", "user.name", "X1 Fixture")
    source_git("add", ".")
    source_git("commit", "-m", "fixture source")
    source_revision = str(source_git("rev-parse", "HEAD"))
    source_tree_sha = str(source_git("rev-parse", "HEAD^{tree}"))

    def source_blob(relative: str) -> dict[str, str]:
        git_bytes = bytes(source_git("show", f"{source_revision}:{relative}", binary=True))
        return {
            "path": relative,
            "source_revision": source_revision,
            "blob_oid": str(source_git("rev-parse", f"{source_revision}:{relative}")),
            "sha256": hashlib.sha256(git_bytes).hexdigest(),
            "working_sha256": sha256_file(source_root / relative),
        }

    source_blobs = [source_blob(relative) for relative in REQUIRED_SOURCE_BLOB_PATHS]
    cfg = X1ResumeConfig.from_path(source_root / DEFAULT_CONFIG_RELATIVE_PATH)
    data_root, source_bindings = governed_fixture(tmp_path, cfg, monkeypatch)
    (source_root / "evidence-only.txt").write_text("descendant\n", encoding="utf-8")
    source_git("add", "evidence-only.txt")
    source_git("commit", "-m", "fixture evidence descendant")

    sample_path = repository_root / "testbed-samples.json"
    canonical_write(
        sample_path,
        {
            "schema_version": "evm.s8_v4.x1_resume_samples.v1",
            "seed": cfg.seed,
            "samples": {
                model.model_id: [
                    [0.0] * model.input_width for _ in range(cfg.sample_rows_per_dataset)
                ]
                for model in cfg.models
            },
            "oracle": {
                model.model_id: {
                    "input_width": model.input_width,
                    "sample_count": cfg.sample_rows_per_dataset,
                    "first_output": 0.5,
                    "output_sha256": canonical_sha256([0.5] * cfg.sample_rows_per_dataset),
                    "outputs": [0.5] * cfg.sample_rows_per_dataset,
                }
                for model in cfg.models
            },
        },
    )
    entries = [
        {
            "path": sample_path.relative_to(repository_root).as_posix(),
            "bytes": sample_path.stat().st_size,
            "sha256": sha256_file(sample_path),
        }
    ]
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
        "schema_version": "evm.s8_v4.x1_resume_model_repository.v1",
        "claim_class": "preliminary_controlled_testbed",
        "credit": "non_credit",
        "config_sha256": cfg.sha256,
        "source_revision": source_revision,
        "source_tree_sha": source_tree_sha,
        "source_blobs": source_blobs,
        "triton_image": cfg.immutable_image,
        "backend": "pytorch",
        "instance_kind": "KIND_GPU",
        "cpu_fallback_allowed": False,
        "model_ids": list(EXPECTED_MODELS),
        "source_bindings": source_bindings,
        "framework": {"torch": "2.13.0+cu126", "cuda_build": "12.6"},
        "samples_sha256": sha256_file(sample_path),
        "entries": entries,
        "repository_sha256": canonical_sha256(entries),
        "profile_identities": profile_identities,
        "model_identities": model_identities,
        "claim_boundary": cfg.claim_boundary,
    }
    manifest_path = repository_root / "model-repository-manifest.json"
    canonical_write(manifest_path, manifest)
    assert len(entries) == 17
    payload["source_identity"] = {
        "branch": "fixture-evidence-descendant",
        "revision": source_revision,
        "tree_sha": source_tree_sha,
    }
    payload["source_blobs"] = json.loads(json.dumps(source_blobs))
    payload["environment"] = {
        "gpu_before": {
            "uuid": cfg.expected_gpu_uuid,
            "name": cfg.expected_gpu_name,
            "memory_used_mib": 100.0,
            "memory_total_mib": 16_384.0,
            "utilization_percent": 0.0,
        },
        "triton_processes_before": [],
        "triton_image": cfg.immutable_image,
        "repository_manifest_sha256": sha256_file(manifest_path),
        "repository_sha256": manifest["repository_sha256"],
        "b0_before": {
            "holder": {"uid": "synthetic", "image": "b0@sha256:fixture", "replicas": 1},
            "cuda": {
                "passed": True,
                "ready": {
                    "architecture": "efficientnet-b0",
                    "candidate_id": "b0-fixture",
                    "class_names": ["anomaly", "normal"],
                    "cuda_available": True,
                    "dataset_version": "fixture-data",
                    "decision_threshold": 0.5,
                    "device": "cuda",
                    "input_size": 224,
                    "model_loaded": True,
                    "model_path": "/fixture/model.pt",
                    "model_sha256": "a" * 64,
                    "service": "evm-b0-production",
                    "status": "ok",
                },
                "prediction": {
                    "candidate_id": "b0-fixture",
                    "confidence": 0.75,
                    "dataset_version": "fixture-data",
                    "decision_threshold": 0.5,
                    "device": "cuda",
                    "image_uri": "/fixture/image.jpg",
                    "latency_ms": 1.0,
                    "model_sha256": "a" * 64,
                    "prediction": "normal",
                    "scores": {"anomaly": 0.25, "normal": 0.75},
                },
            },
        },
        "gpu_lease": {
            "lease_id": "gpu-lease-" + "a" * 32,
            "run_id": payload["suite_id"],
            "scenario_id": "X1-RESUME",
            "model_family": "tabular",
            "purpose": "scale_validation_inference",
            "source_commit": source_revision,
            "fencing_token_sha256": canonical_sha256("f" * 32),
        },
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
            f'nv_inference_count{{model="{model_id}"}} 0\n'
            f'nv_inference_exec_count{{model="{model_id}"}} 0\n'
        )
        after = (
            f'nv_inference_request_success{{model="{model_id}"}} 64\n'
            f'nv_inference_compute_infer_duration_us{{model="{model_id}"}} 64\n'
            f'nv_inference_count{{model="{model_id}"}} 2048\n'
            f'nv_inference_exec_count{{model="{model_id}"}} 64\n'
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
                "gpu_samples": [
                    {
                        "uuid": cfg.expected_gpu_uuid,
                        "name": cfg.expected_gpu_name,
                        "memory_used_mib": 100.0,
                        "memory_total_mib": 16_384.0,
                        "utilization_percent": 1.0,
                    }
                ],
                "gpu_log_lines": [log_line],
                "isolated_request_count": 64,
            },
        )
        item.update(
            {
                "triton_success_delta": 64.0,
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
        cell_spec = next(cell for cell in cfg.cells if cell.cell_id == item["cell_id"])
        bundle = synthetic_attempt_bundle(item["attempt_id"], item["model_mix"])
        attempt_records = bundle["records"]
        terminal_records = bundle["terminal_records"]
        completed_counts = Counter(
            record["model_id"] for record in terminal_records if record["outcome"] == "completed"
        )
        formed_batch = item["cell_id"] == "balanced-concurrent-batch-on"
        before_lines = []
        after_lines = []
        deltas = {}
        for model_id in EXPECTED_MODELS:
            completed_count = float(completed_counts[model_id])
            inference_count = completed_count
            execution_count = (
                float((int(completed_count) + 1) // 2) if formed_batch else completed_count
            )
            values = {
                "success": completed_count,
                "compute_us": completed_count,
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
        inference_total = sum(values["inference_count"] for values in deltas.values())
        execution_total = sum(values["execution_count"] for values in deltas.values())
        item["batching_proof"] = {
            "inference_count_delta": inference_total,
            "execution_count_delta": execution_total,
            "formed_mean_batch_size": inference_total / execution_total,
            "formed_batch_observed": inference_total / execution_total > 1.0,
        }
        window = bundle["measurement_window"]
        admission = bundle["admission"]
        item["metrics"] = summarize_requests(
            offered=admission["offered"],
            admitted=admission["admitted"],
            local_admission_rejected=admission["local_admission_rejected"],
            records=attempt_records,
            measurement_seconds=window["seconds"],
            measurement_start_ns=window["start_ns"],
            measurement_end_ns=window["end_ns"],
            drain_seconds=0.1,
            model_mix=item["model_mix"],
        )
        item["admission_proof"] = bundle["admission_proof"]
        item["cross_model_request_overlap"] = request_interval_overlap(
            attempt_records,
            measurement_start_ns=window["start_ns"],
            measurement_end_ns=window["end_ns"],
        )
        item["triton_metric_deltas"] = deltas
        gpu_samples = [
            {
                "uuid": cfg.expected_gpu_uuid,
                "name": cfg.expected_gpu_name,
                "memory_used_mib": 100.0,
                "memory_total_mib": 16_384.0,
                "utilization_percent": 10.0,
            }
        ]
        item["gpu"] = {
            "sample_count": 1,
            "utilization_max_percent": 10.0,
            "vram_max_mib": 100.0,
        }
        raw_path = attempts_root / f"{item['attempt_id']}.json"
        canonical_write(
            raw_path,
            {
                "attempt_id": item["attempt_id"],
                "cell": {
                    "cell_id": cell_spec.cell_id,
                    "repetitions": cell_spec.repetitions,
                    "model_mix": dict(cell_spec.model_mix),
                    "batching": cell_spec.batching,
                    "client_lanes": cell_spec.client_lanes,
                    "client_workers": cell_spec.client_workers,
                    "analytical_roles": list(cell_spec.analytical_roles),
                },
                "repetition": item["repetition"],
                "records": attempt_records,
                "terminal_records": terminal_records,
                "admission_ledger": bundle["admission_ledger"],
                "measurement_window": window,
                "admission": admission,
                "admission_proof": bundle["admission_proof"],
                "drain_seconds": 0.1,
                "metrics": item["metrics"],
                "triton_metric_deltas": deltas,
                "cross_model_request_overlap": item["cross_model_request_overlap"],
                "batching_proof": item["batching_proof"],
                "gpu_samples": gpu_samples,
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
        "probe_budget_seconds": cfg.cleanup_timeout_seconds,
        "probe_finished_elapsed_seconds": 0.1,
        "probe_started_elapsed_seconds": 0.0,
        "snapshot": prometheus_snapshot,
        "state": "ready",
    }
    final_checks = {
        "holder": payload["environment"]["b0_before"]["holder"],
        "b0_cuda": payload["environment"]["b0_before"]["cuda"],
        "queues": {"active": 0, "leased": 0, "outcome_unknown": 0},
        "gpu": payload["environment"]["gpu_before"],
        "gpu_after_vram_wait": payload["environment"]["gpu_before"],
        "vram_restore_seconds": 0.1,
        "triton_processes": [],
        "containers": {
            "expected_names": ["evm-x1-resume-q0", "evm-x1-resume-off", "evm-x1-resume-on"],
            "present_names": [],
        },
        "ports": {"expected_ports": [18300, 18301, 18302], "listening_ports": []},
        "gpu_lease": {"active": None},
        "prometheus": prometheus_snapshot,
        "prometheus_restore_ready": True,
        "prometheus_restore_samples": [prometheus_sample],
        "prometheus_restore_seconds": 0.1,
        "prometheus_restore_terminal_reason": "ready",
    }
    cleanup_path = suite_root / "cleanup.json"
    canonical_write(
        cleanup_path,
        {"cleanup": payload["cleanup"], "final_checks": final_checks},
    )
    released_payload = {
        "schema_version": "evm.scenario_gpu_lease.v1",
        "lease_id": payload["environment"]["gpu_lease"]["lease_id"],
        "fencing_token": "f" * 32,
        "run_id": payload["suite_id"],
        "scenario_id": "X1-RESUME",
        "model_family": "tabular",
        "lease_purpose": "scale_validation_inference",
        "owner_pid": 1234,
        "source_commit": source_revision,
        "acquired_at": "2026-08-25T00:00:00Z",
        "expires_at": "2026-08-25T02:00:00Z",
        "state": "released",
        "released_at": "2026-08-25T00:30:00Z",
        "release_reason": f"{payload['suite_id']} finished",
    }
    released_path = suite_root / "gpu-lease-released.json"
    canonical_write(released_path, released_payload)
    archive_path = suite_root / "gpu-lease-history-raw.json"
    archive_path.write_text(json.dumps(released_payload, indent=2) + "\n", encoding="ascii")
    payload["cleanup_evidence"] = {
        "path": cleanup_path.relative_to(suite_root).as_posix(),
        "bytes": cleanup_path.stat().st_size,
        "sha256": sha256_file(cleanup_path),
        "final_checks_sha256": canonical_sha256(final_checks),
        "released_gpu_lease": {
            "path": released_path.relative_to(suite_root).as_posix(),
            "bytes": released_path.stat().st_size,
            "sha256": sha256_file(released_path),
            "lease_id": released_payload["lease_id"],
            "run_id": released_payload["run_id"],
            "state": "released",
            "release_reason": released_payload["release_reason"],
        },
        "released_gpu_lease_archive": {
            "path": archive_path.relative_to(suite_root).as_posix(),
            "bytes": archive_path.stat().st_size,
            "sha256": sha256_file(archive_path),
        },
    }

    evidence_path = tmp_path / "evidence.json"
    canonical_write(evidence_path, payload)

    result = validate_evidence(
        payload,
        cfg,
        private_suite_root=suite_root,
        model_repository_root=repository_root,
        source_root=source_root,
        data_root=data_root,
    )
    assert result["private_artifacts_valid"] is True
    report = generate_report(
        payload,
        cfg,
        evidence_path=evidence_path,
        private_suite_root=suite_root,
        model_repository_root=repository_root,
        source_root=source_root,
        data_root=data_root,
    )
    bullet = report["resume_bullets"][0]
    assert "preliminary" in bullet
    assert "API replica" not in bullet
    assert "kernel overlap" not in bullet
    assert "n=3 median" in bullet
    assert "named seeded CUDA test models using governed HIGGS/Criteo inputs" in bullet
    assert "HIGGS/Criteo-derived CUDA models" not in bullet
    assert "no training-quality or model-accuracy claim" in bullet
    assert report["measured"]["topology_comparison_scope"].startswith("compound client-driver")
    assert report["provenance"] == {
        "evidence_canonical_payload_sha256": canonical_sha256(payload),
        "evidence_canonical_file_sha256": sha256_file(evidence_path),
        "evidence_file_sha256": sha256_file(evidence_path),
        "config_sha256": cfg.sha256,
        "source_revision": source_revision,
        "source_tree_sha": source_tree_sha,
        "cleanup_evidence_sha256": payload["cleanup_evidence"]["sha256"],
        "private_validation": {
            "private_artifacts_valid": True,
            "private_attempt_count": 22,
            "repository_entry_count": len(entries),
            "source_revision": source_revision,
            "source_tree_sha": source_tree_sha,
        },
        "private_validation_marker_sha256": canonical_sha256(
            {
                "private_artifacts_valid": True,
                "private_attempt_count": 22,
                "repository_entry_count": len(entries),
                "source_revision": source_revision,
                "source_tree_sha": source_tree_sha,
            }
        ),
    }
    validate_report_binding(
        report,
        payload,
        cfg,
        evidence_path=evidence_path,
        private_suite_root=suite_root,
        model_repository_root=repository_root,
        source_root=source_root,
        data_root=data_root,
    )
    regenerated_report_path = tmp_path / "regenerated-report.json"
    canonical_write(regenerated_report_path, report)
    assert regenerated_report_path.read_bytes() == (canonical(report) + "\n").encode("ascii")
    swapped_report = json.loads(json.dumps(report))
    swapped_report["provenance"]["evidence_canonical_file_sha256"] = "0" * 64
    with pytest.raises(X1ResumeTestbedError, match="x1_resume_report_binding"):
        validate_report_binding(
            swapped_report,
            payload,
            cfg,
            evidence_path=evidence_path,
            private_suite_root=suite_root,
            model_repository_root=repository_root,
            source_root=source_root,
            data_root=data_root,
        )

    original_source_blobs = json.loads(json.dumps(source_blobs))
    original_manifest = json.loads(json.dumps(manifest))

    def rewrite_manifest(raw_manifest: dict[str, object]) -> None:
        canonical_write(manifest_path, raw_manifest)
        payload["environment"]["repository_manifest_sha256"] = sha256_file(manifest_path)

    def assert_source_rejected(pattern: str = "x1_resume_source") -> None:
        with pytest.raises(X1ResumeTestbedError, match=pattern):
            validate_evidence(
                payload,
                cfg,
                private_suite_root=suite_root,
                model_repository_root=repository_root,
                source_root=source_root,
                data_root=data_root,
            )

    payload["source_blobs"] = []
    assert_source_rejected()
    payload.pop("source_blobs")
    assert_source_rejected()
    payload["source_blobs"] = json.loads(json.dumps(original_source_blobs))
    payload["source_blobs"].append(json.loads(json.dumps(original_source_blobs[0])))
    assert_source_rejected()

    payload["source_blobs"] = json.loads(json.dumps(original_source_blobs))
    manifest_without_sources = json.loads(json.dumps(original_manifest))
    manifest_without_sources["source_blobs"] = []
    rewrite_manifest(manifest_without_sources)
    assert_source_rejected()

    replacement = source_blob("replacement-source.py")
    coherent_payload = json.loads(json.dumps(original_source_blobs))
    coherent_manifest = json.loads(json.dumps(original_manifest))
    coherent_payload[0] = replacement
    coherent_manifest["source_blobs"][0] = replacement
    payload["source_blobs"] = coherent_payload
    rewrite_manifest(coherent_manifest)
    assert_source_rejected()

    drift_relative = "scripts/dev/run_s8_v4_x1_resume_testbed.py"
    drift_path = source_root / drift_relative
    original_drift_bytes = drift_path.read_bytes()
    drift_path.write_bytes(original_drift_bytes + b"# uncommitted drift\n")
    drift_sha256 = sha256_file(drift_path)
    drift_payload = json.loads(json.dumps(original_source_blobs))
    drift_manifest = json.loads(json.dumps(original_manifest))
    next(item for item in drift_payload if item["path"] == drift_relative)["working_sha256"] = (
        drift_sha256
    )
    next(item for item in drift_manifest["source_blobs"] if item["path"] == drift_relative)[
        "working_sha256"
    ] = drift_sha256
    payload["source_blobs"] = drift_payload
    rewrite_manifest(drift_manifest)
    try:
        assert_source_rejected("x1_resume_source_blob_identity")
    finally:
        drift_path.write_bytes(original_drift_bytes)

    rewrite_manifest(original_manifest)
    for invalid_path in ("../outside.py", str((tmp_path / "outside.py").resolve())):
        payload["source_blobs"] = json.loads(json.dumps(original_source_blobs))
        payload["source_blobs"][0]["path"] = invalid_path
        assert_source_rejected("x1_resume_source_blob_path")
    payload["source_blobs"] = json.loads(json.dumps(original_source_blobs))
    payload["source_blobs"][0]["blob_oid"] = "0" * 40
    assert_source_rejected("x1_resume_source_blob_identity")
    payload["source_blobs"] = json.loads(json.dumps(original_source_blobs))
    payload["environment"]["gpu_lease"]["source_commit"] = "0" * 40
    assert_source_rejected("x1_resume_source_binding")
    payload["environment"]["gpu_lease"]["source_commit"] = source_revision
    rewrite_manifest(original_manifest)

    for field, invalid_value in (
        ("triton_image", "sha256:wrong"),
        ("triton_processes_before", [{"pid": "1", "process_name": "tritonserver"}]),
    ):
        original_value = json.loads(json.dumps(payload["environment"][field]))
        payload["environment"][field] = invalid_value
        assert_source_rejected("x1_resume_private_environment_contract")
        payload["environment"][field] = original_value
    payload["environment"]["gpu_before"]["uuid"] = "GPU-wrong"
    assert_source_rejected("x1_resume_gpu_sample_identity")
    payload["environment"]["gpu_before"]["uuid"] = cfg.expected_gpu_uuid
    payload["environment"]["gpu_lease"]["purpose"] = "wrong-purpose"
    assert_source_rejected("x1_resume_private_environment_contract")
    payload["environment"]["gpu_lease"]["purpose"] = "scale_validation_inference"

    manifest_wrong_backend = json.loads(json.dumps(original_manifest))
    manifest_wrong_backend["backend"] = "python"
    rewrite_manifest(manifest_wrong_backend)
    assert_source_rejected("x1_resume_private_manifest_contract")

    manifest_missing_sample = json.loads(json.dumps(original_manifest))
    manifest_missing_sample["entries"] = [
        item
        for item in manifest_missing_sample["entries"]
        if item["path"] != "testbed-samples.json"
    ]
    rewrite_manifest(manifest_missing_sample)
    assert_source_rejected("x1_resume_private_repository_entry_set")

    manifest_wrong_sample = json.loads(json.dumps(original_manifest))
    manifest_wrong_sample["samples_sha256"] = "0" * 64
    rewrite_manifest(manifest_wrong_sample)
    assert_source_rejected("x1_resume_private_repository_samples_binding")
    rewrite_manifest(original_manifest)

    q0_item = payload["q0"][0]
    original_q0_config = json.loads(json.dumps(q0_item["triton_config_readback"]))
    q0_item["triton_config_readback"] = {
        "instance_group": [{"kind": "KIND_GPU"}, {"kind": "KIND_CPU"}]
    }
    with pytest.raises(X1ResumeTestbedError, match="q0_gpu_instance_readback"):
        validate_evidence(payload, cfg)
    q0_item["triton_config_readback"] = original_q0_config

    q0_raw_path = suite_root / q0_item["private_raw"]["path"]
    original_q0_raw = json.loads(q0_raw_path.read_bytes())
    q0_metric_tampered = json.loads(json.dumps(original_q0_raw))
    q0_metric_tampered["metrics_after"] = (
        q0_metric_tampered["metrics_after"].replace(" 64\n", " 1\n").replace(" 2048\n", " 1\n")
    )
    canonical_write(q0_raw_path, q0_metric_tampered)
    q0_item["private_raw"].update(
        {"bytes": q0_raw_path.stat().st_size, "sha256": sha256_file(q0_raw_path)}
    )
    q0_item.update(
        {
            "triton_success_delta": 1.0,
            "triton_compute_delta": 1.0,
            "triton_inference_count_delta": 1.0,
            "triton_execution_count_delta": 1.0,
            "metrics_after_sha256": canonical_sha256(q0_metric_tampered["metrics_after"]),
        }
    )
    with pytest.raises(X1ResumeTestbedError, match="q0_request_metric_arithmetic"):
        validate_evidence(payload, cfg)
    canonical_write(q0_raw_path, original_q0_raw)
    q0_item["private_raw"].update(
        {"bytes": q0_raw_path.stat().st_size, "sha256": sha256_file(q0_raw_path)}
    )
    q0_item.update(
        {
            "triton_success_delta": 64.0,
            "triton_compute_delta": 64.0,
            "triton_inference_count_delta": 2048.0,
            "triton_execution_count_delta": 64.0,
            "metrics_after_sha256": canonical_sha256(original_q0_raw["metrics_after"]),
        }
    )

    public_attempt = payload["runs"][0]
    tampered = attempts_root / f"{public_attempt['attempt_id']}.json"
    original_raw = json.loads(tampered.read_bytes())

    def rewrite_attempt(raw_payload: dict[str, object]) -> None:
        canonical_write(tampered, raw_payload)
        public_attempt["private_raw"].update(
            {"bytes": tampered.stat().st_size, "sha256": sha256_file(tampered)}
        )

    latency_tampered = json.loads(json.dumps(original_raw))
    latency_tampered["records"][0]["latency_ms"] = 3.0
    rewrite_attempt(latency_tampered)
    with pytest.raises(X1ResumeTestbedError, match="private_attempt_record_timing"):
        validate_evidence(
            payload,
            cfg,
            private_suite_root=suite_root,
            model_repository_root=repository_root,
            source_root=source_root,
            data_root=data_root,
        )

    duplicate_tampered = json.loads(json.dumps(original_raw))
    duplicate_tampered["records"][1]["request_id"] = duplicate_tampered["records"][0]["request_id"]
    rewrite_attempt(duplicate_tampered)
    with pytest.raises(X1ResumeTestbedError, match="private_attempt_record_identity"):
        validate_evidence(
            payload,
            cfg,
            private_suite_root=suite_root,
            model_repository_root=repository_root,
            source_root=source_root,
            data_root=data_root,
        )

    window_tampered = json.loads(json.dumps(original_raw))
    window_tampered["measurement_window"]["start_ns"] += 1
    window_tampered["measurement_window"]["end_ns"] += 1
    rewrite_attempt(window_tampered)
    with pytest.raises(X1ResumeTestbedError, match="private_admission_ledger_identity"):
        validate_evidence(
            payload,
            cfg,
            private_suite_root=suite_root,
            model_repository_root=repository_root,
            source_root=source_root,
            data_root=data_root,
        )

    triton_tampered = json.loads(json.dumps(original_raw))
    model_id = triton_tampered["records"][0]["model_id"]
    original_success = triton_tampered["triton_metric_deltas"][model_id]["success"]
    triton_tampered["metrics_after"] = triton_tampered["metrics_after"].replace(
        f'nv_inference_request_success{{model="{model_id}"}} {original_success}',
        f'nv_inference_request_success{{model="{model_id}"}} 0',
    )
    rewrite_attempt(triton_tampered)
    with pytest.raises(X1ResumeTestbedError, match="private_attempt_triton_arithmetic"):
        validate_evidence(
            payload,
            cfg,
            private_suite_root=suite_root,
            model_repository_root=repository_root,
            source_root=source_root,
            data_root=data_root,
        )

    records_tampered = json.loads(json.dumps(original_raw))
    records_tampered["records"] = []
    rewrite_attempt(records_tampered)
    with pytest.raises(X1ResumeTestbedError, match="private_measured_terminal_projection"):
        validate_evidence(
            payload,
            cfg,
            private_suite_root=suite_root,
            model_repository_root=repository_root,
            source_root=source_root,
            data_root=data_root,
        )
    summary_tampered = json.loads(json.dumps(original_raw))
    summary_tampered["metrics"]["throughput_rps"] = 999.0
    rewrite_attempt(summary_tampered)
    with pytest.raises(X1ResumeTestbedError, match="private_attempt"):
        validate_evidence(
            payload,
            cfg,
            private_suite_root=suite_root,
            model_repository_root=repository_root,
            source_root=source_root,
            data_root=data_root,
        )
    deltas_tampered = json.loads(json.dumps(original_raw))
    deltas_tampered["triton_metric_deltas"][EXPECTED_MODELS[0]]["success"] = 9.0
    rewrite_attempt(deltas_tampered)
    with pytest.raises(X1ResumeTestbedError, match="private_attempt"):
        validate_evidence(
            payload,
            cfg,
            private_suite_root=suite_root,
            model_repository_root=repository_root,
            source_root=source_root,
            data_root=data_root,
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

    original_public_cleanup = json.loads(json.dumps(payload["cleanup"]))

    def assert_coherent_cleanup_rejected(
        mutated: dict[str, object], cleanup_key: str, pattern: str
    ) -> None:
        rewrite_cleanup(mutated)
        with pytest.raises(X1ResumeTestbedError, match=pattern):
            x1_resume_module._validate_cleanup_evidence(
                payload, mutated["final_checks"], config=cfg
            )
        mutated["cleanup"][cleanup_key] = False
        payload["cleanup"] = json.loads(json.dumps(mutated["cleanup"]))
        rewrite_cleanup(mutated)
        with pytest.raises(X1ResumeTestbedError, match="x1_resume_evidence_invalid:cleanup"):
            validate_evidence(payload, cfg)
        payload["cleanup"] = json.loads(json.dumps(original_public_cleanup))
        rewrite_cleanup(original_cleanup)

    holder_tampered = json.loads(json.dumps(original_cleanup))
    holder_tampered["final_checks"]["holder"]["uid"] = "wrong"
    assert_coherent_cleanup_rejected(
        holder_tampered, "b0_identity_restored", "private_cleanup_runtime"
    )
    queue_tampered = json.loads(json.dumps(original_cleanup))
    queue_tampered["final_checks"]["queues"]["active"] = 1
    assert_coherent_cleanup_rejected(
        queue_tampered, "queue_active_zero", "private_cleanup_recompute"
    )
    process_tampered = json.loads(json.dumps(original_cleanup))
    process_tampered["final_checks"]["triton_processes"] = [{"pid": "1"}]
    rewrite_cleanup(process_tampered)
    with pytest.raises(X1ResumeTestbedError, match="private_cleanup_runtime"):
        x1_resume_module._validate_cleanup_evidence(
            payload, process_tampered["final_checks"], config=cfg
        )
    process_tampered["cleanup"]["triton_gpu_process_residue"] = [{"pid": "1"}]
    payload["cleanup"] = json.loads(json.dumps(process_tampered["cleanup"]))
    rewrite_cleanup(process_tampered)
    with pytest.raises(X1ResumeTestbedError, match="x1_resume_evidence_invalid:cleanup"):
        validate_evidence(payload, cfg)
    payload["cleanup"] = json.loads(json.dumps(original_public_cleanup))
    rewrite_cleanup(original_cleanup)
    container_tampered = json.loads(json.dumps(original_cleanup))
    container_tampered["final_checks"]["containers"]["present_names"] = ["evm-x1-resume-q0"]
    assert_coherent_cleanup_rejected(
        container_tampered, "container_absent", "private_cleanup_runtime"
    )
    port_tampered = json.loads(json.dumps(original_cleanup))
    port_tampered["final_checks"]["ports"]["listening_ports"] = [18300]
    assert_coherent_cleanup_rejected(port_tampered, "ports_absent", "private_cleanup_runtime")
    lease_tampered = json.loads(json.dumps(original_cleanup))
    lease_tampered["final_checks"]["gpu_lease"]["active"] = {"lease_id": "residue"}
    assert_coherent_cleanup_rejected(lease_tampered, "gpu_lease_absent", "private_cleanup_runtime")
    gpu_tampered = json.loads(json.dumps(original_cleanup))
    gpu_tampered["final_checks"]["gpu_after_vram_wait"]["uuid"] = "GPU-wrong"
    assert_coherent_cleanup_rejected(
        gpu_tampered, "gpu_identity_restored", "private_cleanup_recompute"
    )
    vram_tampered = json.loads(json.dumps(original_cleanup))
    vram_tampered["final_checks"]["gpu_after_vram_wait"]["memory_used_mib"] = 10_000.0
    assert_coherent_cleanup_rejected(
        vram_tampered, "gpu_vram_restored", "private_cleanup_recompute"
    )
    cuda_tampered = json.loads(json.dumps(original_cleanup))
    cuda_tampered["final_checks"]["b0_cuda"]["ready"]["device"] = "cpu"
    assert_coherent_cleanup_rejected(cuda_tampered, "b0_cuda_restored", "b0_cuda_runtime")
    missing_check = json.loads(json.dumps(original_cleanup))
    missing_check["final_checks"].pop("ports")
    rewrite_cleanup(missing_check)
    with pytest.raises(X1ResumeTestbedError, match="private_cleanup_schema"):
        x1_resume_module._validate_cleanup_evidence(
            payload, missing_check["final_checks"], config=cfg
        )
    rewrite_cleanup(original_cleanup)

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
        x1_resume_module._validate_cleanup_evidence(
            payload, terminal_not_ready["final_checks"], config=cfg
        )

    elapsed_over_timeout = json.loads(json.dumps(original_cleanup))
    elapsed_over_timeout["final_checks"]["prometheus_restore_seconds"] = 121.0
    elapsed_over_timeout["final_checks"]["prometheus_restore_samples"][-1][
        "probe_finished_elapsed_seconds"
    ] = 121.0
    rewrite_cleanup(elapsed_over_timeout)
    with pytest.raises(X1ResumeTestbedError, match="private_prometheus_cleanup"):
        x1_resume_module._validate_cleanup_evidence(
            payload, elapsed_over_timeout["final_checks"], config=cfg
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
            "probe_budget_seconds": cfg.cleanup_timeout_seconds,
            "probe_finished_elapsed_seconds": 0.05,
            "probe_started_elapsed_seconds": 0.0,
            "snapshot": wrong_snapshot,
            "state": "invalid_snapshot",
        },
        prometheus_sample,
    ]
    rewrite_cleanup(illegal_transition)
    with pytest.raises(X1ResumeTestbedError, match="private_prometheus_cleanup"):
        x1_resume_module._validate_cleanup_evidence(
            payload, illegal_transition["final_checks"], config=cfg
        )

    rewrite_cleanup(original_cleanup)
    cleanup_tampered = json.loads(json.dumps(original_cleanup))
    cleanup_tampered["cleanup"]["container_absent"] = False
    rewrite_cleanup(cleanup_tampered)
    payload["cleanup"] = json.loads(json.dumps(cleanup_tampered["cleanup"]))
    with pytest.raises(X1ResumeTestbedError, match="private_cleanup"):
        x1_resume_module._validate_cleanup_evidence(
            payload, cleanup_tampered["final_checks"], config=cfg
        )
    payload["cleanup"] = json.loads(json.dumps(original_public_cleanup))
    rewrite_cleanup(original_cleanup)


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


def test_canonical_json_and_public_output_targets_fail_closed(tmp_path: Path) -> None:
    payload = {"alpha": 1, "nested": {"beta": True}}
    output = tmp_path / "evidence.json"
    x1_resume_module.canonical_write_once(output, payload)
    assert x1_resume_module.load_canonical_json(output, label="positive") == payload
    with pytest.raises(X1ResumeTestbedError, match="x1_resume_output_exists"):
        x1_resume_module.canonical_write_once(output, payload)

    whitespace = tmp_path / "whitespace.json"
    whitespace.write_bytes(b'{"alpha": 1}\n')
    with pytest.raises(X1ResumeTestbedError, match="x1_resume_json_not_canonical"):
        x1_resume_module.load_canonical_json(whitespace, label="whitespace")

    duplicate = tmp_path / "duplicate.json"
    duplicate.write_bytes(b'{"alpha":1,"alpha":2}\n')
    with pytest.raises(X1ResumeTestbedError, match="x1_resume_json_duplicate_key"):
        x1_resume_module.load_canonical_json(duplicate, label="duplicate")

    alias = output.parent / "nested" / ".." / output.name
    with pytest.raises(X1ResumeTestbedError, match="x1_resume_public_output_collision"):
        x1_resume_module.ensure_distinct_output_targets(output, alias)
    if os.name == "nt":
        with pytest.raises(X1ResumeTestbedError, match="x1_resume_public_output_collision"):
            x1_resume_module.ensure_distinct_output_targets(
                output, output.with_name(output.name.upper())
            )

    actual = tmp_path / "actual"
    actual.mkdir()
    linked = tmp_path / "linked-output"
    try:
        linked.symlink_to(actual, target_is_directory=True)
    except OSError:
        if os.name != "nt":
            raise
        subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(linked), str(actual)],
            check=True,
            capture_output=True,
        )
    with pytest.raises(X1ResumeTestbedError, match="x1_resume_public_output_collision"):
        x1_resume_module.ensure_distinct_output_targets(
            actual / "result.json", linked / "result.json"
        )


def test_request_overlap_requires_strict_in_window_intersection() -> None:
    def record(model_id: str, started_ns: int, finished_ns: int) -> dict[str, object]:
        return {
            "model_id": model_id,
            "outcome": "completed",
            "started_ns": started_ns,
            "finished_ns": finished_ns,
            "latency_ms": (finished_ns - started_ns) / 1e6,
        }

    endpoint_only = [
        record(EXPECTED_MODELS[0], 100, 200),
        record(EXPECTED_MODELS[1], 200, 300),
    ]
    assert request_interval_overlap(endpoint_only)["observed"] is False

    tail_only = [
        record(EXPECTED_MODELS[0], 90, 180),
        record(EXPECTED_MODELS[1], 120, 170),
    ]
    assert (
        request_interval_overlap(tail_only, measurement_start_ns=0, measurement_end_ns=100)[
            "observed"
        ]
        is False
    )

    in_window = [
        record(EXPECTED_MODELS[0], 100, 300),
        record(EXPECTED_MODELS[1], 200, 400),
    ]
    assert (
        request_interval_overlap(in_window, measurement_start_ns=150, measurement_end_ns=350)[
            "observed"
        ]
        is True
    )

    with pytest.raises(X1ResumeTestbedError, match="x1_resume_request_interval_invalid"):
        request_interval_overlap([record(EXPECTED_MODELS[0], 100, 100)])


@pytest.mark.parametrize("invalid_value", ["nan", "inf", "-1", "not-a-number"])
def test_triton_absolute_counter_values_fail_closed(invalid_value: str) -> None:
    model_id = EXPECTED_MODELS[0]
    text = (
        f'nv_inference_request_success{{model="{model_id}"}} {invalid_value}\n'
        f'nv_inference_compute_infer_duration_us{{model="{model_id}"}} 1\n'
        f'nv_inference_count{{model="{model_id}"}} 1\n'
        f'nv_inference_exec_count{{model="{model_id}"}} 1\n'
    )
    with pytest.raises(
        X1ResumeTestbedError,
        match="x1_resume_private_metric_(parse|value)",
    ):
        x1_resume_module._triton_metrics_for_model(text, model_id)

    valid = {
        "success": 10.0,
        "compute_us": 10.0,
        "inference_count": 10.0,
        "execution_count": 10.0,
    }
    decreased = dict(valid)
    decreased["success"] = 9.0
    with pytest.raises(X1ResumeTestbedError, match="x1_resume_private_metric_counter"):
        x1_resume_module._triton_metric_deltas(valid, decreased, model_id=model_id)


def test_raw_admission_terminal_partition_and_schedule_fail_closed() -> None:
    attempt_id = "x1-resume-20260825T000000Z-aaaaaaaa-solo-logistic-r1-00000001"
    model_mix = {EXPECTED_MODELS[0]: 1.0}
    measurement_start_ns = 2_000_000_000
    measurement_end_ns = 3_000_000_000

    def offer(sequence: int, enqueued_ns: int, decision: str) -> dict[str, object]:
        return {
            "global_sequence": sequence,
            "request_id": f"{attempt_id}-{sequence}",
            "model_id": EXPECTED_MODELS[0],
            "phase": "warmup" if enqueued_ns < measurement_start_ns else "measured",
            "enqueued_ns": enqueued_ns,
            "decision_ns": enqueued_ns + 1_000,
            "decision": decision,
            "reason": "local_queue_capacity" if decision == "accepted" else "local_queue_full",
        }

    def terminal(source: dict[str, object]) -> dict[str, object]:
        started_ns = int(source["decision_ns"]) + 1_000
        finished_ns = started_ns + 1_000_000
        return {
            "request_id": source["request_id"],
            "model_id": source["model_id"],
            "worker_id": 0,
            "outcome": "completed",
            "status": 200,
            "enqueued_ns": source["enqueued_ns"],
            "started_ns": started_ns,
            "finished_ns": finished_ns,
            "queue_wait_ms": (started_ns - int(source["enqueued_ns"])) / 1e6,
            "latency_ms": 1.0,
            "global_sequence": source["global_sequence"],
            "phase": source["phase"],
        }

    ledger = [
        offer(0, 1_500_000_000, "accepted"),
        offer(1, 2_100_000_000, "accepted"),
        offer(2, 2_200_000_000, "rejected"),
    ]
    terminals = [terminal(ledger[0]), terminal(ledger[1])]
    records = [
        {
            key: value
            for key, value in terminals[1].items()
            if key not in {"global_sequence", "phase"}
        }
    ]
    admission = {"offered": 2, "admitted": 1, "local_admission_rejected": 1}
    completed, all_completed, identities, _proof = x1_resume_module._validate_attempt_records(
        records,
        terminals,
        ledger,
        attempt_id=attempt_id,
        model_mix=model_mix,
        warmup_seconds=1,
        measurement_start_ns=measurement_start_ns,
        measurement_end_ns=measurement_end_ns,
        admission=admission,
    )
    assert completed == Counter({EXPECTED_MODELS[0]: 1})
    assert all_completed == Counter({EXPECTED_MODELS[0]: 2})
    assert identities == {item["request_id"] for item in ledger}

    def rejected(
        mutated_records: object,
        mutated_terminals: object,
        mutated_ledger: object,
        reason: str,
    ) -> None:
        with pytest.raises(X1ResumeTestbedError, match=reason):
            x1_resume_module._validate_attempt_records(
                mutated_records,
                mutated_terminals,
                mutated_ledger,
                attempt_id=attempt_id,
                model_mix=model_mix,
                warmup_seconds=1,
                measurement_start_ns=measurement_start_ns,
                measurement_end_ns=measurement_end_ns,
                admission=admission,
            )

    sequence_gap = json.loads(json.dumps(ledger))
    sequence_gap[1]["global_sequence"] = 9
    rejected(records, terminals, sequence_gap, "private_admission_ledger_identity")

    wrong_schedule = json.loads(json.dumps(ledger))
    wrong_schedule[1]["model_id"] = EXPECTED_MODELS[1]
    rejected(records, terminals, wrong_schedule, "private_admission_ledger_identity")

    warmup_timing = json.loads(json.dumps(terminals))
    warmup_timing[0]["latency_ms"] = 2.0
    rejected(records, warmup_timing, ledger, "private_terminal_record_binding")

    missing_terminal = json.loads(json.dumps(terminals[1:]))
    rejected(records, missing_terminal, ledger, "private_terminal_identity_set")

    rejected_terminal = json.loads(json.dumps(terminals))
    rejected_terminal.append(terminal(ledger[2]))
    rejected(records, rejected_terminal, ledger, "private_terminal_record_binding")


def test_result_commit_binds_evidence_and_report_git_blobs(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()

    def git(*args: str) -> str:
        return subprocess.run(
            ["git", *args],
            cwd=repository,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

    git("init")
    git("config", "user.email", "x1-result@example.invalid")
    git("config", "user.name", "X1 Result")
    (repository / "source.txt").write_text("source\n", encoding="ascii", newline="\n")
    git("add", "source.txt")
    git("commit", "-m", "source")
    source_revision = git("rev-parse", "HEAD")

    evidence = repository / "docs" / "evidence.json"
    report = repository / "docs" / "report.json"
    canonical_write(evidence, {"kind": "evidence"})
    canonical_write(report, {"kind": "report"})
    git("add", "docs/evidence.json", "docs/report.json")
    git("commit", "-m", "result")
    result_revision = git("rev-parse", "HEAD")

    binding = x1_resume_module.validate_result_git_binding(
        evidence_path=evidence,
        report_path=report,
        source_root=repository,
        source_revision=source_revision,
        result_revision=result_revision,
    )
    assert binding["result_revision"] == result_revision
    assert binding["source_revision"] == source_revision
    assert set(binding["files"]) == {"evidence", "report"}

    report.write_text('{"kind":"tampered"}\n', encoding="ascii", newline="\n")
    with pytest.raises(X1ResumeTestbedError, match="x1_resume_result_working_blob"):
        x1_resume_module.validate_result_git_binding(
            evidence_path=evidence,
            report_path=report,
            source_root=repository,
            source_revision=source_revision,
            result_revision=result_revision,
        )


def test_runbook_revalidates_committed_report_and_private_roots() -> None:
    runbook = (ROOT / "docs/status/2026-08-25-x1-resume-testbed-v1-runbook.md").read_text(
        encoding="utf-8"
    )
    assert runbook.count("--data-root $DataRoot") == 4
    assert "--report $Report" in runbook
    assert "--require-git-binding" in runbook
    assert "--result-revision HEAD" in runbook
    assert "Remove-Item -LiteralPath $RevalidatedReport" in runbook


def test_public_attempt_identity_and_run_set_are_exact() -> None:
    payload = complete_payload()
    first = payload["runs"][0]
    second = payload["runs"][1]
    second["attempt_id"] = first["attempt_id"]
    with pytest.raises(X1ResumeTestbedError, match="attempt_identity"):
        validate_evidence(payload, config())

    payload = complete_payload()
    payload["runs"].append(json.loads(json.dumps(payload["runs"][0])))
    with pytest.raises(X1ResumeTestbedError, match="physical_run_(matrix|count)|attempt_identity"):
        validate_evidence(payload, config())


def test_b0_holder_and_cuda_identity_reject_coherent_empty_values() -> None:
    holder = {"uid": "b0-uid", "image": "b0@sha256:fixture", "replicas": 1}
    assert x1_resume_module._validate_b0_holder(holder, label="positive") == holder
    for invalid in ({}, {**holder, "replicas": True}, {**holder, "uid": ""}):
        with pytest.raises(X1ResumeTestbedError, match="x1_resume_b0_holder"):
            x1_resume_module._validate_b0_holder(invalid, label="mutation")

    cuda = {
        "passed": True,
        "ready": {
            "architecture": "efficientnet-b0",
            "candidate_id": "b0-fixture",
            "class_names": ["anomaly", "normal"],
            "cuda_available": True,
            "dataset_version": "fixture-data",
            "decision_threshold": 0.5,
            "device": "cuda",
            "input_size": 224,
            "model_loaded": True,
            "model_path": "/fixture/model.pt",
            "model_sha256": "a" * 64,
            "service": "evm-b0-production",
            "status": "ok",
        },
        "prediction": {
            "candidate_id": "b0-fixture",
            "confidence": 0.75,
            "dataset_version": "fixture-data",
            "decision_threshold": 0.5,
            "device": "cuda",
            "image_uri": "/fixture/image.jpg",
            "latency_ms": 1.0,
            "model_sha256": "a" * 64,
            "prediction": "normal",
            "scores": {"anomaly": 0.25, "normal": 0.75},
        },
    }
    identity = x1_resume_module._validate_b0_cuda(cuda, label="positive")
    assert identity["device"] == "cuda"
    for mutation in (
        {},
        {**cuda, "passed": False},
        {**cuda, "ready": {**cuda["ready"], "device": "cpu"}},
        {**cuda, "prediction": {**cuda["prediction"], "candidate_id": "wrong"}},
    ):
        with pytest.raises(X1ResumeTestbedError, match="x1_resume_b0_cuda"):
            x1_resume_module._validate_b0_cuda(mutation, label="mutation")


def test_released_gpu_lease_is_bound_to_the_exact_suite_and_archive() -> None:
    suite_id = "x1-resume-20260825T000000Z-aaaaaaaa"
    lease_id = "gpu-lease-" + "a" * 32
    fencing_token = "f" * 32
    source_revision = "b" * 40
    payload = {
        "suite_id": suite_id,
        "environment": {
            "gpu_lease": {
                "lease_id": lease_id,
                "run_id": suite_id,
                "scenario_id": "X1-RESUME",
                "model_family": "tabular",
                "purpose": "scale_validation_inference",
                "source_commit": source_revision,
                "fencing_token_sha256": canonical_sha256(fencing_token),
            }
        },
    }
    released = {
        "schema_version": "evm.scenario_gpu_lease.v1",
        "lease_id": lease_id,
        "fencing_token": fencing_token,
        "run_id": suite_id,
        "scenario_id": "X1-RESUME",
        "model_family": "tabular",
        "lease_purpose": "scale_validation_inference",
        "owner_pid": 1234,
        "source_commit": source_revision,
        "acquired_at": "2026-08-25T00:00:00Z",
        "expires_at": "2026-08-25T02:00:00Z",
        "state": "released",
        "released_at": "2026-08-25T00:30:00Z",
        "release_reason": f"{suite_id} finished",
    }
    identity = {
        "path": "gpu-lease-released.json",
        "bytes": 1,
        "sha256": "c" * 64,
        "lease_id": lease_id,
        "run_id": suite_id,
        "state": "released",
        "release_reason": f"{suite_id} finished",
    }
    archive_identity = {
        "path": "gpu-lease-history-raw.json",
        "bytes": 1,
        "sha256": "d" * 64,
    }
    x1_resume_module._validate_released_lease(
        payload, released, released, identity, archive_identity
    )

    wrong_suite = json.loads(json.dumps(released))
    wrong_suite["run_id"] = "x1-resume-20260825T000000Z-bbbbbbbb"
    with pytest.raises(X1ResumeTestbedError, match="x1_resume_private_released_lease"):
        x1_resume_module._validate_released_lease(
            payload, wrong_suite, wrong_suite, identity, archive_identity
        )

    wrong_archive = json.loads(json.dumps(released))
    wrong_archive["release_reason"] = "coherent-but-wrong"
    with pytest.raises(X1ResumeTestbedError, match="x1_resume_private_released_lease"):
        x1_resume_module._validate_released_lease(
            payload, released, wrong_archive, identity, archive_identity
        )

    for field, value in (
        ("released_at", "not-a-timestamp"),
        ("released_at", "2026-08-24T23:59:59Z"),
        ("released_at", "2026-08-25T02:00:01Z"),
        ("fencing_token", "not-a-fencing-token"),
        ("source_commit", "c" * 40),
        ("state", "active"),
        ("release_reason", "coherent-but-wrong"),
    ):
        mutated = json.loads(json.dumps(released))
        mutated[field] = value
        mutated_identity = json.loads(json.dumps(identity))
        if field in mutated_identity:
            mutated_identity[field] = value
        with pytest.raises(X1ResumeTestbedError, match="x1_resume_private_released_lease"):
            x1_resume_module._validate_released_lease(
                payload, mutated, mutated, mutated_identity, archive_identity
            )


def test_governed_model_source_replacement_is_not_accepted_coherently(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = config()
    data_root, bindings = governed_fixture(tmp_path, cfg, monkeypatch)
    manifest = {
        "source_bindings": bindings,
        "framework": {"torch": "2.13.0+cu126", "cuda_build": "12.6"},
    }
    x1_resume_module.validate_governed_source_bindings(manifest, data_root=data_root, config=cfg)

    identity = x1_resume_module.GOVERNED_SOURCE_IDENTITIES["s3_logistic"]
    source_path = data_root / str(identity["path"])
    source_path.write_bytes(source_path.read_bytes() + b"coherent replacement")
    coherent = json.loads(json.dumps(manifest))
    coherent_binding = coherent["source_bindings"]["higgs_logistic_regression"]
    coherent_binding["source_sha256"] = sha256_file(source_path)
    coherent_binding["source_bytes"] = source_path.stat().st_size
    with pytest.raises(X1ResumeTestbedError, match="x1_resume_governed_identity"):
        x1_resume_module.validate_governed_source_bindings(
            coherent, data_root=data_root, config=cfg
        )

    invalid_framework = json.loads(json.dumps(manifest))
    invalid_framework["framework"] = {"torch": "", "cuda_build": "12.6", "extra": "x"}
    with pytest.raises(X1ResumeTestbedError, match="x1_resume_governed_binding_schema"):
        x1_resume_module.validate_governed_source_bindings(
            invalid_framework, data_root=data_root, config=cfg
        )
