from __future__ import annotations

import json
import hashlib
import os
import runpy
import subprocess
from collections import Counter
from dataclasses import replace
from itertools import groupby
from pathlib import Path
from types import SimpleNamespace
from typing import Any

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
    MODEL_CLAIM_CONTRACT,
    MODEL_DESCRIPTION,
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
    render_triton_server_command,
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
            "shape": [100000, 28],
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


def triton_config_readback(
    model_id: str, cfg: X1ResumeConfig, *, dynamic_batching: bool = False
) -> dict[str, object]:
    input_width = next(model.input_width for model in cfg.models if model.model_id == model_id)
    result: dict[str, object] = {
        "name": model_id,
        "backend": "pytorch",
        "max_batch_size": "32",
        "version_policy": {"specific": {"versions": ["1"]}},
        "input": [{"name": "FEATURES__0", "data_type": "TYPE_FP32", "dims": [str(input_width)]}],
        "output": [{"name": "SCORE__0", "data_type": "TYPE_FP32", "dims": ["1"]}],
        "instance_group": [{"kind": "KIND_GPU", "count": "1", "gpus": ["0"]}],
        "model_warmup": [],
        "optimization": {
            "eager_batching": False,
            "gather_kernel_buffer_threshold": "0",
            "input_pinned_memory": {"enable": True},
            "output_pinned_memory": {"enable": True},
            "priority": "PRIORITY_DEFAULT",
        },
    }
    if dynamic_batching:
        result["dynamic_batching"] = {
            "preferred_batch_size": ["4", "8"],
            "max_queue_delay_microseconds": "10000",
        }
    return result


def triton_runtime_readiness(cfg: X1ResumeConfig) -> dict[str, object]:
    ready_index = [
        {"name": model_id, "version": "1", "state": "READY", "reason": ""}
        for model_id in EXPECTED_MODELS
    ]
    return {
        "server_health": {
            "live": {"endpoint": "/v2/health/live", "status": 200},
            "ready": {"endpoint": "/v2/health/ready", "status": 200},
        },
        "repository_index_full": json.loads(json.dumps(ready_index)),
        "repository_index_ready": ready_index,
        "model_ready": {
            model_id: {
                "endpoint": f"/v2/models/{model_id}/versions/1/ready",
                "status": 200,
            }
            for model_id in EXPECTED_MODELS
        },
        "model_metadata": {
            model.model_id: {
                "endpoint": f"/v2/models/{model.model_id}/versions/1",
                "payload": {
                    "name": model.model_id,
                    "versions": ["1"],
                    "platform": "pytorch_libtorch",
                    "inputs": [
                        {
                            "name": "FEATURES__0",
                            "datatype": "FP32",
                            "shape": [-1, model.input_width],
                        }
                    ],
                    "outputs": [{"name": "SCORE__0", "datatype": "FP32", "shape": [-1, 1]}],
                },
            }
            for model in cfg.models
        },
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
    warmup_count = 7_200
    warmup_accepted = 4
    measured_offered = 24_000
    measured_accepted = 400
    total = warmup_count + measured_offered
    for sequence in range(total):
        phase = "warmup" if sequence < warmup_count else "measured"
        measured_sequence = sequence - warmup_count
        enqueued_ns = (
            10_000_000_000 + sequence * (10_000_000_000 // warmup_count)
            if phase == "warmup"
            else measurement_start_ns + measured_sequence * 1_250_000
        )
        accepted = (
            sequence < warmup_accepted
            if phase == "warmup"
            else measured_sequence < measured_accepted
        )
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
            "oracle_valid": True,
            "expected_output": 0.5,
            "observed_output": 0.5,
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
    warmup_ledger = [item for item in admission_ledger if item["phase"] == "warmup"]
    warmup_terminals = [item for item in terminal_records if item["phase"] == "warmup"]
    admission_proof = {
        "issued_count": len(admission_ledger),
        "warmup_expected_offered": 8_000,
        "warmup_min_offered": 7_200,
        "warmup_max_offered": 8_400,
        "warmup_offered": warmup_count,
        "warmup_accepted": warmup_accepted,
        "warmup_rejected": warmup_count - warmup_accepted,
        "warmup_completed": warmup_accepted,
        "warmup_http_5xx": 0,
        "warmup_other_errors": 0,
        "warmup_loss": 0,
        "warmup_duplicates": 0,
        "warmup_first_enqueued_ns": warmup_ledger[0]["enqueued_ns"],
        "warmup_last_enqueued_ns": warmup_ledger[-1]["enqueued_ns"],
        "warmup_observed_span_ns": (
            int(warmup_ledger[-1]["enqueued_ns"]) - int(warmup_ledger[0]["enqueued_ns"])
        ),
        "warmup_offered_by_model": {
            model_id: sum(item["model_id"] == model_id for item in warmup_ledger)
            for model_id in EXPECTED_MODELS
        },
        "warmup_accepted_by_model": {
            model_id: sum(
                item["model_id"] == model_id and item["decision"] == "accepted"
                for item in warmup_ledger
            )
            for model_id in EXPECTED_MODELS
        },
        "warmup_rejected_by_model": {
            model_id: sum(
                item["model_id"] == model_id and item["decision"] == "rejected"
                for item in warmup_ledger
            )
            for model_id in EXPECTED_MODELS
        },
        "warmup_completed_by_model": {
            model_id: sum(item["model_id"] == model_id for item in warmup_terminals)
            for model_id in EXPECTED_MODELS
        },
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
    assert cfg.model_claim_contract == MODEL_CLAIM_CONTRACT
    assert MODEL_DESCRIPTION in cfg.claim_boundary
    for key in MODEL_CLAIM_CONTRACT:
        if key != "model_description":
            assert f"{key}=false" in cfg.claim_boundary
    assert "not deployed API replicas" in cfg.claim_boundary
    assert "kernel-overlap evidence unless a profiler directly proves overlap" in cfg.claim_boundary
    command = render_triton_server_command(trace_enabled=True)
    assert "--trace-config=mode=triton" in command
    assert "--trace-config=triton,file=/evidence/triton-trace.json" in command
    assert "--trace-config=rate=64" in command
    runner = (ROOT / "scripts/dev/run_s8_v4_x1_resume_testbed.py").read_text(encoding="utf-8")
    assert "trace_enabled=False" in runner

    mutated_claims = dict(cfg.model_claim_contract)
    mutated_claims["prepared_model_equivalence_claim"] = True
    with pytest.raises(X1ResumeTestbedError, match="x1_resume_model_claim_contract"):
        replace(cfg, model_claim_contract=mutated_claims).validate()


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


def test_runner_normalizes_omitted_empty_repository_reason(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = runpy.run_path(
        str(ROOT / "scripts/dev/run_s8_v4_x1_resume_testbed.py"),
        run_name="x1_resume_runner_repository_index_test",
    )
    payload = [{"name": model_id, "version": "1", "state": "READY"} for model_id in EXPECTED_MODELS]

    class Response:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> list[dict[str, str]]:
            return payload

    monkeypatch.setattr(runner["requests"], "post", lambda *args, **kwargs: Response())
    normalized = runner["fetch_repository_index"](config())

    assert normalized == [dict(item, reason="") for item in payload]
    assert x1_resume_module.triton_repository_index_exact(normalized)


def test_runner_preserves_nonempty_repository_reason(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = runpy.run_path(
        str(ROOT / "scripts/dev/run_s8_v4_x1_resume_testbed.py"),
        run_name="x1_resume_runner_repository_reason_test",
    )
    payload = [
        {
            "name": model_id,
            "version": "1",
            "state": "READY",
            "reason": "" if index else "unexpected",
        }
        for index, model_id in enumerate(EXPECTED_MODELS)
    ]

    class Response:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> list[dict[str, str]]:
            return payload

    monkeypatch.setattr(runner["requests"], "post", lambda *args, **kwargs: Response())
    observed = runner["fetch_repository_index"](config())

    assert observed == payload
    assert not x1_resume_module.triton_repository_index_exact(observed)


@pytest.fixture
def prepared_runner_repository(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[dict[str, Any], X1ResumeConfig, Path, Path]:
    cfg = config()
    repository_root = tmp_path / "prepared-repository"
    data_root, source_bindings = governed_fixture(tmp_path, cfg, monkeypatch)
    prepare = runpy.run_path(
        str(ROOT / "scripts/dev/prepare_s8_v4_x1_resume_testbed.py"),
        run_name="x1_resume_prepare_to_runner_integration",
    )
    runner = runpy.run_path(
        str(ROOT / "scripts/dev/run_s8_v4_x1_resume_testbed.py"),
        run_name="x1_resume_runner_repository_integration",
    )

    class FakeModule:
        def __init__(self, model_id: str) -> None:
            self.model_id = model_id

        def save(self, path: str) -> None:
            Path(path).write_bytes(f"prepared:{self.model_id}".encode())

        def __call__(self, values: Any) -> Any:
            import torch

            return torch.full((len(values), 1), 0.5, dtype=torch.float32)

    def parse_args() -> object:
        return type(
            "Args",
            (),
            {"config": CONFIG, "data_root": data_root, "output": repository_root},
        )()

    def build_models(
        active_config: X1ResumeConfig, _data_root: Path
    ) -> tuple[dict[str, Any], dict[str, list[list[float]]]]:
        samples = {
            model.model_id: [
                [0.0] * model.input_width for _ in range(active_config.sample_rows_per_dataset)
            ]
            for model in active_config.models
        }
        return {
            "modules": {
                model.model_id: FakeModule(model.model_id) for model in active_config.models
            },
            "bindings": source_bindings,
        }, samples

    def git(*args: str) -> str:
        if args == ("status", "--porcelain"):
            return ""
        if args == ("rev-parse", "HEAD"):
            return "a" * 40
        if args == ("rev-parse", "HEAD^{tree}"):
            return "b" * 40
        raise AssertionError(args)

    def committed_blob(relative: str, revision: str) -> dict[str, str]:
        return {
            "path": relative,
            "source_revision": revision,
            "blob_oid": "c" * 40,
            "sha256": "d" * 64,
            "working_sha256": "d" * 64,
        }

    prepare_globals = prepare["main"].__globals__
    prepare_globals["parse_args"] = parse_args
    prepare_globals["build_models"] = build_models
    prepare_globals["git"] = git
    prepare_globals["committed_blob"] = committed_blob
    assert prepare["main"]() == 0
    return runner, cfg, repository_root, data_root


def test_runner_loader_accepts_actual_prepare_manifest(
    prepared_runner_repository: tuple[dict[str, Any], X1ResumeConfig, Path, Path],
) -> None:
    runner, cfg, repository_root, data_root = prepared_runner_repository
    manifest, samples = runner["load_and_validate_repository"](repository_root, cfg, data_root)
    assert manifest["model_claim_contract"] == MODEL_CLAIM_CONTRACT
    assert manifest["model_claim_contract_sha256"] == canonical_sha256(MODEL_CLAIM_CONTRACT)
    assert "framework" not in manifest
    assert len(manifest["entries"]) == 17
    assert {
        path.relative_to(repository_root).as_posix()
        for path in repository_root.rglob("*")
        if path.is_file()
    } == {
        *x1_resume_module.EXPECTED_REPOSITORY_ENTRY_PATHS,
        x1_resume_module.MODEL_REPOSITORY_MANIFEST_NAME,
    }
    assert {
        path.relative_to(repository_root).as_posix()
        for path in repository_root.rglob("*")
        if path.is_dir()
    } == set(x1_resume_module.EXPECTED_REPOSITORY_DIRECTORY_PATHS)
    assert set(samples["samples"]) == set(EXPECTED_MODELS)


def test_triton_server_command_is_explicit_exact_model_set() -> None:
    command = x1_resume_module.render_triton_server_command(trace_enabled=False)
    assert "--model-control-mode=explicit" in command
    assert command.count("--load-model=") == len(EXPECTED_MODELS)
    assert {
        token.removeprefix("--load-model=")
        for token in command.split()
        if token.startswith("--load-model=")
    } == set(EXPECTED_MODELS)
    assert "--load-model=*" not in command


def test_triton_mount_contract_is_exact_and_structured(tmp_path: Path) -> None:
    repository = tmp_path / "batch-off"
    evidence_root = tmp_path / "evidence"
    repository.mkdir()
    evidence_root.mkdir()
    positive = [
        {
            "Type": "bind",
            "Source": str(repository),
            "Destination": "/models",
            "Mode": "ro",
            "RW": False,
            "Propagation": "rprivate",
        },
        {
            "Type": "bind",
            "Source": str(evidence_root),
            "Destination": "/evidence",
            "Mode": "rw",
            "RW": True,
            "Propagation": "rprivate",
        },
    ]
    assert (
        x1_resume_module.validate_triton_container_mounts(
            positive, repository=repository, evidence_root=evidence_root
        )
        == positive
    )
    mutations = []
    for destination, field, value in (
        ("/models", "RW", True),
        ("/models", "Mode", "rw"),
        ("/models", "Propagation", "rshared"),
        ("/models", "Type", "volume"),
        ("/models", "Source", str(tmp_path / "wrong")),
        ("/evidence", "RW", False),
        ("/evidence", "Mode", "ro"),
        ("/evidence", "Propagation", ""),
    ):
        mutated = json.loads(json.dumps(positive))
        next(item for item in mutated if item["Destination"] == destination)[field] = value
        mutations.append(mutated)
    extra_field = json.loads(json.dumps(positive))
    extra_field[0]["Unexpected"] = "coherent-but-forbidden"
    mutations.append(extra_field)
    mutations.append([*json.loads(json.dumps(positive)), dict(positive[0])])
    for mutated in mutations:
        with pytest.raises(X1ResumeTestbedError, match="x1_resume_container_mount"):
            x1_resume_module.validate_triton_container_mounts(
                mutated, repository=repository, evidence_root=evidence_root
            )


def test_runner_start_triton_enforces_exact_mount_inspect(
    prepared_runner_repository: tuple[dict[str, Any], X1ResumeConfig, Path, Path],
    tmp_path: Path,
) -> None:
    runner, cfg, repository_root, _data_root = prepared_runner_repository
    repository = repository_root / "batch-off"
    evidence_root = tmp_path / "runtime-evidence"
    evidence_root.mkdir()
    positive = [
        {
            "Type": "bind",
            "Source": str(repository),
            "Destination": "/models",
            "Mode": "ro",
            "RW": False,
            "Propagation": "rprivate",
        },
        {
            "Type": "bind",
            "Source": str(evidence_root),
            "Destination": "/evidence",
            "Mode": "rw",
            "RW": True,
            "Propagation": "rprivate",
        },
    ]

    def bind_inspect(mounts: list[dict[str, object]]) -> None:
        def fake_run(command: list[str], **_kwargs: object) -> SimpleNamespace:
            stdout = json.dumps([{"Mounts": mounts}]) if command[1] == "inspect" else ""
            return SimpleNamespace(stdout=stdout)

        runner["start_triton"].__globals__["run"] = fake_run

    bind_inspect(positive)
    result = runner["start_triton"](
        cfg, repository, evidence_root, "x1-mount-positive", trace_enabled=False
    )
    assert result["mounts"] == positive
    for field, value in (
        ("Mode", "rw"),
        ("Propagation", "rshared"),
        ("Unexpected", "forbidden"),
    ):
        mutated = json.loads(json.dumps(positive))
        mutated[0][field] = value
        bind_inspect(mutated)
        with pytest.raises(X1ResumeTestbedError, match="x1_resume_container_mount"):
            runner["start_triton"](
                cfg, repository, evidence_root, "x1-mount-negative", trace_enabled=False
            )


def test_runner_model_config_readback_uses_shared_exact_gate(
    prepared_runner_repository: tuple[dict[str, Any], X1ResumeConfig, Path, Path],
) -> None:
    runner, cfg, _repository_root, _data_root = prepared_runner_repository

    def bind_configs(configs: dict[str, dict[str, object]]) -> None:
        runner["verify_model_configs"].__globals__["request_json"] = (
            lambda url, **_kwargs: json.loads(
                json.dumps(
                    configs[
                        next(model_id for model_id in EXPECTED_MODELS if f"/{model_id}/" in url)
                    ]
                )
            )
        )

    positive = {
        model_id: triton_config_readback(model_id, cfg, dynamic_batching=True)
        for model_id in EXPECTED_MODELS
    }
    bind_configs(positive)
    assert set(runner["verify_model_configs"](cfg, "on")) == set(EXPECTED_MODELS)

    for case in ("preferred", "delay", "response-cache", "extra-nested"):
        mutated = json.loads(json.dumps(positive))
        selected = mutated[EXPECTED_MODELS[0]]
        if case == "preferred":
            selected["dynamic_batching"]["preferred_batch_size"] = ["999"]
        elif case == "delay":
            selected["dynamic_batching"]["max_queue_delay_microseconds"] = "1"
        elif case == "response-cache":
            selected["response_cache"] = {"enable": True}
        else:
            selected["dynamic_batching"]["unexpected"] = "field"
        bind_configs(mutated)
        with pytest.raises(X1ResumeTestbedError, match="x1_resume_model_config_readback"):
            runner["verify_model_configs"](cfg, "on")


def test_triton_repository_and_version_metadata_are_exact() -> None:
    cfg = config()
    positive = triton_runtime_readiness(cfg)
    assert x1_resume_module.triton_runtime_readiness_exact(positive, config=cfg)
    mutations = []
    for collection in ("repository_index_full", "repository_index_ready"):
        wrong_version = json.loads(json.dumps(positive))
        wrong_version[collection][0]["version"] = "2"
        mutations.append(wrong_version)
        extra = json.loads(json.dumps(positive))
        extra[collection].append({"name": "extra", "version": "1", "state": "READY", "reason": ""})
        mutations.append(extra)
    not_ready = json.loads(json.dumps(positive))
    not_ready["repository_index_ready"][0]["state"] = "LOADING"
    mutations.append(not_ready)
    metadata_version = json.loads(json.dumps(positive))
    metadata_version["model_metadata"][EXPECTED_MODELS[0]]["payload"]["versions"] = ["2"]
    mutations.append(metadata_version)
    metadata_platform = json.loads(json.dumps(positive))
    metadata_platform["model_metadata"][EXPECTED_MODELS[0]]["payload"]["platform"] = "python"
    mutations.append(metadata_platform)
    metadata_shape = json.loads(json.dumps(positive))
    metadata_shape["model_metadata"][EXPECTED_MODELS[0]]["payload"]["inputs"][0]["shape"][1] = 999
    mutations.append(metadata_shape)
    metadata_extra = json.loads(json.dumps(positive))
    metadata_extra["model_metadata"][EXPECTED_MODELS[0]]["payload"]["extra"] = True
    mutations.append(metadata_extra)
    server_not_ready = json.loads(json.dumps(positive))
    server_not_ready["server_health"]["ready"]["status"] = 503
    mutations.append(server_not_ready)
    server_status_float = json.loads(json.dumps(positive))
    server_status_float["server_health"]["ready"]["status"] = 200.0
    mutations.append(server_status_float)
    model_not_ready = json.loads(json.dumps(positive))
    model_not_ready["model_ready"][EXPECTED_MODELS[0]]["status"] = 503
    mutations.append(model_not_ready)
    model_ready_unversioned = json.loads(json.dumps(positive))
    model_ready_unversioned["model_ready"][EXPECTED_MODELS[0]]["endpoint"] = (
        f"/v2/models/{EXPECTED_MODELS[0]}/ready"
    )
    mutations.append(model_ready_unversioned)
    assert all(
        not x1_resume_module.triton_runtime_readiness_exact(item, config=cfg) for item in mutations
    )


@pytest.mark.parametrize(
    "case",
    [
        "claim-missing",
        "bool-recomputed-sha",
        "integer-zero-recomputed-sha",
        "sha-only",
        "claim-extra",
    ],
)
def test_runner_loader_rejects_prepare_manifest_claim_mutation(
    prepared_runner_repository: tuple[dict[str, Any], X1ResumeConfig, Path, Path],
    case: str,
) -> None:
    runner, cfg, repository_root, data_root = prepared_runner_repository
    manifest_path = repository_root / "model-repository-manifest.json"
    manifest = json.loads(manifest_path.read_bytes())
    if case == "claim-missing":
        manifest.pop("model_claim_contract")
    elif case == "bool-recomputed-sha":
        manifest["model_claim_contract"]["model_accuracy_claim"] = True
        manifest["model_claim_contract_sha256"] = canonical_sha256(manifest["model_claim_contract"])
    elif case == "integer-zero-recomputed-sha":
        manifest["model_claim_contract"]["model_accuracy_claim"] = 0
        manifest["model_claim_contract_sha256"] = canonical_sha256(manifest["model_claim_contract"])
    elif case == "sha-only":
        manifest["model_claim_contract_sha256"] = "f" * 64
    else:
        manifest["model_claim_contract"]["unexpected_claim"] = False
        manifest["model_claim_contract_sha256"] = canonical_sha256(manifest["model_claim_contract"])
    canonical_write(manifest_path, manifest)
    with pytest.raises(X1ResumeTestbedError, match="x1_resume_repository_manifest_contract"):
        runner["load_and_validate_repository"](repository_root, cfg, data_root)


@pytest.mark.parametrize("field", ["profile_identities", "model_identities"])
@pytest.mark.parametrize("operation", ["add", "drop"])
def test_runner_loader_rejects_identity_key_set_drift(
    prepared_runner_repository: tuple[dict[str, Any], X1ResumeConfig, Path, Path],
    field: str,
    operation: str,
) -> None:
    runner, cfg, repository_root, data_root = prepared_runner_repository
    manifest_path = repository_root / "model-repository-manifest.json"
    manifest = json.loads(manifest_path.read_bytes())
    if operation == "add":
        manifest[field]["unexpected"] = {}
    else:
        manifest[field].pop(next(iter(manifest[field])))
    canonical_write(manifest_path, manifest)
    with pytest.raises(X1ResumeTestbedError, match="x1_resume_repository_manifest_contract"):
        runner["load_and_validate_repository"](repository_root, cfg, data_root)
    with pytest.raises(X1ResumeTestbedError, match="x1_resume_private_manifest_contract"):
        x1_resume_module._validate_manifest_contract(manifest, cfg)


def test_runner_loader_rejects_removed_framework_provenance(
    prepared_runner_repository: tuple[dict[str, Any], X1ResumeConfig, Path, Path],
) -> None:
    runner, cfg, repository_root, data_root = prepared_runner_repository
    manifest_path = repository_root / "model-repository-manifest.json"
    manifest = json.loads(manifest_path.read_bytes())
    manifest["framework"] = {"torch": "99.99", "cuda_build": "99.99"}
    canonical_write(manifest_path, manifest)
    with pytest.raises(X1ResumeTestbedError, match="x1_resume_repository_manifest_contract"):
        runner["load_and_validate_repository"](repository_root, cfg, data_root)


@pytest.mark.parametrize(
    "bytes_value", ["1", 1.0, True], ids=["numeric-string", "float", "boolean"]
)
def test_runner_loader_rejects_non_integer_repository_bytes(
    prepared_runner_repository: tuple[dict[str, Any], X1ResumeConfig, Path, Path],
    bytes_value: object,
) -> None:
    runner, cfg, repository_root, data_root = prepared_runner_repository
    manifest_path = repository_root / "model-repository-manifest.json"
    manifest = json.loads(manifest_path.read_bytes())
    manifest["entries"][0]["bytes"] = bytes_value
    manifest["repository_sha256"] = canonical_sha256(manifest["entries"])
    canonical_write(manifest_path, manifest)
    with pytest.raises(X1ResumeTestbedError, match="x1_resume_repository_manifest_contract"):
        runner["load_and_validate_repository"](repository_root, cfg, data_root)
    with pytest.raises(X1ResumeTestbedError, match="x1_resume_private_manifest_contract"):
        x1_resume_module._validate_manifest_contract(manifest, cfg)


@pytest.mark.parametrize("entry_count", [8.0, "8", True], ids=["float", "string", "boolean"])
def test_runner_loader_rejects_non_integer_profile_entry_count(
    prepared_runner_repository: tuple[dict[str, Any], X1ResumeConfig, Path, Path],
    entry_count: object,
) -> None:
    runner, cfg, repository_root, data_root = prepared_runner_repository
    manifest_path = repository_root / "model-repository-manifest.json"
    manifest = json.loads(manifest_path.read_bytes())
    manifest["profile_identities"]["off"]["entry_count"] = entry_count
    canonical_write(manifest_path, manifest)
    with pytest.raises(X1ResumeTestbedError, match="x1_resume_repository_manifest_contract"):
        runner["load_and_validate_repository"](repository_root, cfg, data_root)
    with pytest.raises(X1ResumeTestbedError, match="x1_resume_private_manifest_contract"):
        x1_resume_module._validate_manifest_contract(manifest, cfg)


@pytest.mark.parametrize("case", ["extra", "wrong-order", "non-string"])
def test_runner_loader_rejects_model_id_contract_drift(
    prepared_runner_repository: tuple[dict[str, Any], X1ResumeConfig, Path, Path],
    case: str,
) -> None:
    runner, cfg, repository_root, data_root = prepared_runner_repository
    manifest_path = repository_root / "model-repository-manifest.json"
    manifest = json.loads(manifest_path.read_bytes())
    if case == "extra":
        manifest["model_ids"].append("unexpected")
    elif case == "wrong-order":
        manifest["model_ids"][0], manifest["model_ids"][1] = (
            manifest["model_ids"][1],
            manifest["model_ids"][0],
        )
    else:
        manifest["model_ids"][0] = 0
    canonical_write(manifest_path, manifest)
    with pytest.raises(X1ResumeTestbedError, match="x1_resume_repository_manifest_contract"):
        runner["load_and_validate_repository"](repository_root, cfg, data_root)


@pytest.mark.parametrize(
    "field,case",
    [
        ("profile_identities", "extra-nested"),
        ("profile_identities", "non-string-sha"),
        ("model_identities", "extra-nested"),
        ("model_identities", "missing-nested"),
        ("model_identities", "non-string-sha"),
    ],
)
def test_runner_loader_rejects_nested_identity_schema_drift(
    prepared_runner_repository: tuple[dict[str, Any], X1ResumeConfig, Path, Path],
    field: str,
    case: str,
) -> None:
    runner, cfg, repository_root, data_root = prepared_runner_repository
    manifest_path = repository_root / "model-repository-manifest.json"
    manifest = json.loads(manifest_path.read_bytes())
    identity = next(iter(manifest[field].values()))
    if case == "extra-nested":
        identity["unexpected"] = "x"
    elif case == "missing-nested":
        identity.pop("artifact_sha256")
    else:
        sha_field = "repository_sha256" if field == "profile_identities" else "artifact_sha256"
        identity[sha_field] = 0
    canonical_write(manifest_path, manifest)
    with pytest.raises(X1ResumeTestbedError, match="x1_resume_repository_manifest_contract"):
        runner["load_and_validate_repository"](repository_root, cfg, data_root)


@pytest.mark.parametrize(
    "relative_path",
    [
        "undeclared.bin",
        ".hidden",
        "batch-off/higgs_logistic_regression/2/model.pt",
        "batch-off/extra_model/1/model.pt",
    ],
    ids=["root-extra", "hidden-file", "version-2", "extra-model"],
)
def test_runner_loader_rejects_undeclared_repository_file(
    prepared_runner_repository: tuple[dict[str, Any], X1ResumeConfig, Path, Path],
    relative_path: str,
) -> None:
    runner, cfg, repository_root, data_root = prepared_runner_repository
    unexpected = repository_root / relative_path
    unexpected.parent.mkdir(parents=True, exist_ok=True)
    unexpected.write_bytes(b"undeclared")
    with pytest.raises(
        X1ResumeTestbedError, match="x1_resume_private_repository_physical_file_set"
    ):
        runner["load_and_validate_repository"](repository_root, cfg, data_root)


def test_runner_loader_rejects_missing_repository_file(
    prepared_runner_repository: tuple[dict[str, Any], X1ResumeConfig, Path, Path],
) -> None:
    runner, cfg, repository_root, data_root = prepared_runner_repository
    (repository_root / "batch-off/higgs_logistic_regression/1/model.pt").unlink()
    with pytest.raises(
        X1ResumeTestbedError, match="x1_resume_private_repository_physical_file_set"
    ):
        runner["load_and_validate_repository"](repository_root, cfg, data_root)


def test_runner_loader_rejects_unexpected_empty_repository_directory(
    prepared_runner_repository: tuple[dict[str, Any], X1ResumeConfig, Path, Path],
) -> None:
    runner, cfg, repository_root, data_root = prepared_runner_repository
    (repository_root / "batch-on/empty-model").mkdir()
    with pytest.raises(
        X1ResumeTestbedError, match="x1_resume_private_repository_physical_directory_set"
    ):
        runner["load_and_validate_repository"](repository_root, cfg, data_root)


@pytest.mark.parametrize(
    "relative_path",
    [
        "batch-off/higgs_logistic_regression/1/model.pt",
        "batch-off/higgs_logistic_regression/1",
    ],
    ids=["file-link", "directory-link"],
)
def test_runner_loader_rejects_repository_symlink_alias(
    prepared_runner_repository: tuple[dict[str, Any], X1ResumeConfig, Path, Path],
    monkeypatch: pytest.MonkeyPatch,
    relative_path: str,
) -> None:
    runner, cfg, repository_root, data_root = prepared_runner_repository
    target = repository_root / relative_path
    original_is_symlink = Path.is_symlink
    monkeypatch.setattr(
        Path,
        "is_symlink",
        lambda path: path == target or original_is_symlink(path),
    )
    with pytest.raises(X1ResumeTestbedError, match="x1_resume_private_repository_node_type"):
        runner["load_and_validate_repository"](repository_root, cfg, data_root)


def test_runner_loader_rejects_repository_root_alias(
    prepared_runner_repository: tuple[dict[str, Any], X1ResumeConfig, Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner, cfg, repository_root, data_root = prepared_runner_repository
    original_is_symlink = Path.is_symlink
    monkeypatch.setattr(
        Path,
        "is_symlink",
        lambda path: path == repository_root or original_is_symlink(path),
    )
    with pytest.raises(X1ResumeTestbedError, match="x1_resume_private_repository_root"):
        runner["load_and_validate_repository"](repository_root, cfg, data_root)


def test_runner_loader_rejects_repository_root_reparse_point(
    prepared_runner_repository: tuple[dict[str, Any], X1ResumeConfig, Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner, cfg, repository_root, data_root = prepared_runner_repository
    original_lstat = Path.lstat
    root_stat = original_lstat(repository_root)

    def lstat(path: Path) -> object:
        if path == repository_root:
            return SimpleNamespace(
                st_mode=root_stat.st_mode,
                st_file_attributes=getattr(
                    x1_resume_module.stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400
                ),
            )
        return original_lstat(path)

    monkeypatch.setattr(Path, "lstat", lstat)
    with pytest.raises(X1ResumeTestbedError, match="x1_resume_private_repository_root"):
        runner["load_and_validate_repository"](repository_root, cfg, data_root)


def test_runner_loader_rejects_nonregular_repository_node(
    prepared_runner_repository: tuple[dict[str, Any], X1ResumeConfig, Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner, cfg, repository_root, data_root = prepared_runner_repository
    monkeypatch.setattr(x1_resume_module.stat, "S_ISREG", lambda _mode: False)
    with pytest.raises(X1ResumeTestbedError, match="x1_resume_private_repository_node_type"):
        runner["load_and_validate_repository"](repository_root, cfg, data_root)


@pytest.mark.parametrize(
    "appended",
    [
        "optimization { }\n",
        'model_warmup [ { name: "warm" batch_size: 1 } ]\n',
        "response_cache { enable: true }\n",
        'rate_limiter { resources [ { name: "gpu" count: 1 } ] }\n',
        "version_policy { latest { num_versions: 2 } }\n",
    ],
    ids=["optimization", "warmup", "cache", "rate-limit", "version-policy"],
)
def test_runner_loader_rejects_coherently_rehashed_config_bytes(
    prepared_runner_repository: tuple[dict[str, Any], X1ResumeConfig, Path, Path],
    appended: str,
) -> None:
    runner, cfg, repository_root, data_root = prepared_runner_repository
    manifest_path = repository_root / "model-repository-manifest.json"
    manifest = json.loads(manifest_path.read_bytes())
    relative = "batch-off/higgs_logistic_regression/config.pbtxt"
    config_path = repository_root / relative
    config_path.write_text(
        config_path.read_text(encoding="utf-8") + appended,
        encoding="utf-8",
        newline="\n",
    )
    entry = next(item for item in manifest["entries"] if item["path"] == relative)
    entry.update({"bytes": config_path.stat().st_size, "sha256": sha256_file(config_path)})
    selected = [item for item in manifest["entries"] if item["path"].startswith("batch-off/")]
    manifest["profile_identities"]["off"] = {
        "entry_count": len(selected),
        "repository_sha256": canonical_sha256(selected),
    }
    manifest["model_identities"]["off:higgs_logistic_regression"]["config_sha256"] = entry["sha256"]
    manifest["repository_sha256"] = canonical_sha256(manifest["entries"])
    canonical_write(manifest_path, manifest)
    with pytest.raises(X1ResumeTestbedError, match="x1_resume_private_repository_config_bytes"):
        runner["load_and_validate_repository"](repository_root, cfg, data_root)


@pytest.mark.parametrize(
    "case,reason",
    [
        ("source-bytes-float", "x1_resume_governed_s3_binding"),
        ("replay-bytes-float", "x1_resume_governed_s3_binding"),
        ("replay-shape-float", "x1_resume_governed_s3_binding"),
        ("replay-list-pairs", "x1_resume_governed_s3_binding"),
        ("false-as-zero", "x1_resume_governed_s5_binding"),
        ("seed-float", "x1_resume_governed_s5_binding"),
    ],
)
def test_runner_loader_rejects_typed_source_binding_aliases(
    prepared_runner_repository: tuple[dict[str, Any], X1ResumeConfig, Path, Path],
    case: str,
    reason: str,
) -> None:
    runner, cfg, repository_root, data_root = prepared_runner_repository
    manifest_path = repository_root / "model-repository-manifest.json"
    manifest = json.loads(manifest_path.read_bytes())
    s3 = manifest["source_bindings"]["higgs_logistic_regression"]
    s5 = manifest["source_bindings"]["criteo_dlrm_lite"]
    if case == "source-bytes-float":
        s3["source_bytes"] = float(s3["source_bytes"])
    elif case == "replay-bytes-float":
        s3["replay"]["replay_bytes"] = float(s3["replay"]["replay_bytes"])
    elif case == "replay-shape-float":
        s3["replay"]["replay_shape"][0] = float(s3["replay"]["replay_shape"][0])
    elif case == "replay-list-pairs":
        s3["replay"] = list(s3["replay"].items())
    elif case == "false-as-zero":
        s5["training_or_quality_claim"] = 0
    else:
        s5["seed"] = float(s5["seed"])
    canonical_write(manifest_path, manifest)
    with pytest.raises(X1ResumeTestbedError, match=reason):
        runner["load_and_validate_repository"](repository_root, cfg, data_root)


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
        batching=cfg.batching["off"],
    )
    batch_on = triton_config_readback(model.model_id, cfg, dynamic_batching=True)
    assert triton_gpu_instance_exact(
        batch_on,
        model_id=model.model_id,
        input_width=model.input_width,
        batching=cfg.batching["on"],
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
    missing_version = json.loads(json.dumps(positive))
    missing_version.pop("version_policy")
    mutations.append(missing_version)
    wrong_version = json.loads(json.dumps(positive))
    wrong_version["version_policy"]["specific"]["versions"] = ["2"]
    mutations.append(wrong_version)
    extra_version = json.loads(json.dumps(positive))
    extra_version["version_policy"]["specific"]["versions"] = ["1", "2"]
    mutations.append(extra_version)
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

    off_with_dynamic = json.loads(json.dumps(positive))
    off_with_dynamic["dynamic_batching"] = {}
    mutations.append(off_with_dynamic)
    for case in (
        "preferred",
        "delay",
        "preferred-type",
        "delay-type",
        "dynamic-extra",
        "response-cache",
        "warmup",
        "optimization-extra",
    ):
        mutated = json.loads(json.dumps(batch_on))
        if case == "preferred":
            mutated["dynamic_batching"]["preferred_batch_size"] = ["999"]
        elif case == "delay":
            mutated["dynamic_batching"]["max_queue_delay_microseconds"] = "1"
        elif case == "preferred-type":
            mutated["dynamic_batching"]["preferred_batch_size"] = [4, 8]
        elif case == "delay-type":
            mutated["dynamic_batching"]["max_queue_delay_microseconds"] = 10_000
        elif case == "dynamic-extra":
            mutated["dynamic_batching"]["preserve_ordering"] = True
        elif case == "response-cache":
            mutated["response_cache"] = {"enable": True}
        elif case == "warmup":
            mutated["model_warmup"] = [{"name": "unexpected"}]
        else:
            mutated["optimization"]["unexpected"] = True
        assert not triton_gpu_instance_exact(
            mutated,
            model_id=model.model_id,
            input_width=model.input_width,
            batching=cfg.batching["on"],
        )

    assert len(mutations) == 19
    assert all(
        not triton_gpu_instance_exact(
            mutated,
            model_id=model.model_id,
            input_width=model.input_width,
            batching=cfg.batching["off"],
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
            model_spec = next(model for model in cfg.models if model.model_id == model_id)
            model_config.write_text(
                x1_resume_module.render_triton_model_config(
                    model_id,
                    model_spec.input_width,
                    batching=cfg.batching[profile],
                ),
                encoding="utf-8",
                newline="\n",
            )
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
        "samples_sha256": sha256_file(sample_path),
        "entries": entries,
        "repository_sha256": canonical_sha256(entries),
        "model_claim_contract": dict(MODEL_CLAIM_CONTRACT),
        "model_claim_contract_sha256": canonical_sha256(MODEL_CLAIM_CONTRACT),
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

    def runtime_contract(profile: str, batching: str, evidence_root: Path) -> dict[str, object]:
        runtime_path = evidence_root / "runtime-contract.json"
        canonical_write(
            runtime_path,
            {
                "schema_version": "evm.s8_v4.x1_resume_runtime_contract.v1",
                "profile": profile,
                "batching": batching,
                "server_command": x1_resume_module.render_triton_server_command(
                    trace_enabled=profile == "q0_isolated"
                ),
                "mounts": [
                    {
                        "Type": "bind",
                        "Source": str(repository_root / f"batch-{batching}"),
                        "Destination": "/models",
                        "Mode": "ro",
                        "RW": False,
                        "Propagation": "rprivate",
                    },
                    {
                        "Type": "bind",
                        "Source": str(evidence_root),
                        "Destination": "/evidence",
                        "Mode": "rw",
                        "RW": True,
                        "Propagation": "rprivate",
                    },
                ],
                "runtime_readiness": triton_runtime_readiness(cfg),
                "model_configs": {
                    model_id: {
                        "endpoint": f"/v2/models/{model_id}/versions/1/config",
                        "payload": triton_config_readback(
                            model_id, cfg, dynamic_batching=batching == "on"
                        ),
                    }
                    for model_id in EXPECTED_MODELS
                },
            },
        )
        return {
            "path": runtime_path.relative_to(suite_root).as_posix(),
            "bytes": runtime_path.stat().st_size,
            "sha256": sha256_file(runtime_path),
        }

    payload["profile_evidence"] = {
        "q0_isolated": {
            "runtime_contract": runtime_contract("q0_isolated", "off", q0_root),
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
            "runtime_contract": runtime_contract(profile, profile, profile_root),
            "log": {
                "path": profile_log.relative_to(suite_root).as_posix(),
                "bytes": profile_log.stat().st_size,
                "sha256": sha256_file(profile_log),
            },
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
    assert "70% offered hot-model mix" in bullet
    assert "no training-quality or model-accuracy claim" in bullet
    assert report["mandatory_disclosure"] == {
        **MODEL_CLAIM_CONTRACT,
        "boundary": cfg.claim_boundary,
    }
    assert report["measured"]["model_claim_contract"] == MODEL_CLAIM_CONTRACT
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
        "model_claim_contract": dict(MODEL_CLAIM_CONTRACT),
        "model_claim_contract_sha256": canonical_sha256(MODEL_CLAIM_CONTRACT),
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
    claim_tampered = json.loads(json.dumps(report))
    claim_tampered["mandatory_disclosure"]["model_accuracy_claim"] = True
    claim_tampered["provenance"]["model_claim_contract"]["model_accuracy_claim"] = True
    claim_tampered["provenance"]["model_claim_contract_sha256"] = canonical_sha256(
        claim_tampered["provenance"]["model_claim_contract"]
    )
    with pytest.raises(X1ResumeTestbedError, match="x1_resume_report_binding"):
        validate_report_binding(
            claim_tampered,
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

    def assert_report_rejected(pattern: str) -> None:
        canonical_write(evidence_path, payload)
        with pytest.raises(X1ResumeTestbedError, match=pattern):
            generate_report(
                payload,
                cfg,
                evidence_path=evidence_path,
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

    coherent_claim_drift = json.loads(json.dumps(original_manifest))
    coherent_claim_drift["model_claim_contract"]["model_accuracy_claim"] = True
    coherent_claim_drift["model_claim_contract_sha256"] = canonical_sha256(
        coherent_claim_drift["model_claim_contract"]
    )
    rewrite_manifest(coherent_claim_drift)
    assert_source_rejected("x1_resume_private_manifest_contract")
    assert_report_rejected("x1_resume_private_manifest_contract")

    coherent_false_zero = json.loads(json.dumps(original_manifest))
    coherent_false_zero["model_claim_contract"]["model_accuracy_claim"] = 0
    coherent_false_zero["model_claim_contract_sha256"] = canonical_sha256(
        coherent_false_zero["model_claim_contract"]
    )
    rewrite_manifest(coherent_false_zero)
    assert_source_rejected("x1_resume_private_manifest_contract")
    assert_report_rejected("x1_resume_private_manifest_contract")

    coherent_binding_zero = json.loads(json.dumps(original_manifest))
    coherent_binding_zero["source_bindings"]["criteo_dlrm_lite"]["training_or_quality_claim"] = 0
    rewrite_manifest(coherent_binding_zero)
    assert_source_rejected("x1_resume_governed_s5_binding")
    assert_report_rejected("x1_resume_governed_s5_binding")

    config_relative = "batch-off/higgs_logistic_regression/config.pbtxt"
    config_path = repository_root / config_relative
    original_config_bytes = config_path.read_bytes()
    config_path.write_bytes(original_config_bytes + b"response_cache { enable: true }\n")
    coherent_config_drift = json.loads(json.dumps(original_manifest))
    config_entry = next(
        item for item in coherent_config_drift["entries"] if item["path"] == config_relative
    )
    config_entry.update({"bytes": config_path.stat().st_size, "sha256": sha256_file(config_path)})
    off_entries = [
        item for item in coherent_config_drift["entries"] if item["path"].startswith("batch-off/")
    ]
    coherent_config_drift["profile_identities"]["off"] = {
        "entry_count": len(off_entries),
        "repository_sha256": canonical_sha256(off_entries),
    }
    coherent_config_drift["model_identities"]["off:higgs_logistic_regression"]["config_sha256"] = (
        config_entry["sha256"]
    )
    coherent_config_drift["repository_sha256"] = canonical_sha256(coherent_config_drift["entries"])
    rewrite_manifest(coherent_config_drift)
    payload["environment"]["repository_sha256"] = coherent_config_drift["repository_sha256"]
    assert_source_rejected("x1_resume_private_repository_config_bytes")
    assert_report_rejected("x1_resume_private_repository_config_bytes")
    config_path.write_bytes(original_config_bytes)
    payload["environment"]["repository_sha256"] = original_manifest["repository_sha256"]
    rewrite_manifest(original_manifest)

    for profile, case, pattern in (
        ("q0_isolated", "wildcard-command", "x1_resume_private_runtime_contract"),
        ("q0_isolated", "writable-model-mount", "x1_resume_container_mount"),
        ("q0_isolated", "model-mode-rw", "x1_resume_container_mount"),
        ("q0_isolated", "shared-propagation", "x1_resume_container_mount"),
        ("q0_isolated", "mount-extra-field", "x1_resume_container_mount"),
        ("q0_isolated", "extra-repository-model", "x1_resume_private_runtime_contract"),
        ("q0_isolated", "unversioned-metadata", "x1_resume_private_runtime_contract"),
        ("q0_isolated", "config-version-two", "x1_resume_private_runtime_configs"),
        ("on", "preferred-999", "x1_resume_private_runtime_configs"),
        ("on", "delay-one", "x1_resume_private_runtime_configs"),
        ("on", "response-cache", "x1_resume_private_runtime_configs"),
        ("on", "dynamic-extra-field", "x1_resume_private_runtime_configs"),
    ):
        runtime_identity = payload["profile_evidence"][profile]["runtime_contract"]
        runtime_path = suite_root / runtime_identity["path"]
        original_runtime_contract = json.loads(runtime_path.read_bytes())
        mutated_runtime = json.loads(json.dumps(original_runtime_contract))
        if case == "wildcard-command":
            mutated_runtime["server_command"] = mutated_runtime["server_command"].replace(
                f"--load-model={EXPECTED_MODELS[0]}", "--load-model=*"
            )
        elif case == "writable-model-mount":
            next(item for item in mutated_runtime["mounts"] if item["Destination"] == "/models")[
                "RW"
            ] = True
        elif case == "model-mode-rw":
            next(item for item in mutated_runtime["mounts"] if item["Destination"] == "/models")[
                "Mode"
            ] = "rw"
        elif case == "shared-propagation":
            next(item for item in mutated_runtime["mounts"] if item["Destination"] == "/models")[
                "Propagation"
            ] = "rshared"
        elif case == "mount-extra-field":
            next(item for item in mutated_runtime["mounts"] if item["Destination"] == "/models")[
                "Unexpected"
            ] = "coherent"
        elif case == "extra-repository-model":
            extra_index = {
                "name": "extra-model",
                "version": "1",
                "state": "READY",
                "reason": "",
            }
            mutated_runtime["runtime_readiness"]["repository_index_full"].append(extra_index)
            mutated_runtime["runtime_readiness"]["repository_index_ready"].append(extra_index)
        elif case == "unversioned-metadata":
            mutated_runtime["runtime_readiness"]["model_metadata"][EXPECTED_MODELS[0]][
                "endpoint"
            ] = f"/v2/models/{EXPECTED_MODELS[0]}"
        elif case == "config-version-two":
            mutated_runtime["model_configs"][EXPECTED_MODELS[0]]["payload"]["version_policy"][
                "specific"
            ]["versions"] = ["2"]
        elif case == "preferred-999":
            mutated_runtime["model_configs"][EXPECTED_MODELS[0]]["payload"]["dynamic_batching"][
                "preferred_batch_size"
            ] = ["999"]
        elif case == "delay-one":
            mutated_runtime["model_configs"][EXPECTED_MODELS[0]]["payload"]["dynamic_batching"][
                "max_queue_delay_microseconds"
            ] = "1"
        elif case == "response-cache":
            mutated_runtime["model_configs"][EXPECTED_MODELS[0]]["payload"]["response_cache"] = {
                "enable": True
            }
        else:
            mutated_runtime["model_configs"][EXPECTED_MODELS[0]]["payload"]["dynamic_batching"][
                "unexpected"
            ] = "field"
        canonical_write(runtime_path, mutated_runtime)
        runtime_identity.update(
            {"bytes": runtime_path.stat().st_size, "sha256": sha256_file(runtime_path)}
        )
        assert_source_rejected(pattern)
        assert_report_rejected(pattern)
        canonical_write(runtime_path, original_runtime_contract)
        runtime_identity.update(
            {"bytes": runtime_path.stat().st_size, "sha256": sha256_file(runtime_path)}
        )

    undeclared_version = repository_root / "batch-off/higgs_logistic_regression/2/model.pt"
    undeclared_version.parent.mkdir()
    undeclared_version.write_bytes(b"undeclared-version")
    assert_source_rejected("x1_resume_private_repository_physical_file_set")
    assert_report_rejected("x1_resume_private_repository_physical_file_set")
    undeclared_version.unlink()
    undeclared_version.parent.rmdir()

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
            "oracle_valid": True,
            "expected_output": 0.5,
            "observed_output": 0.5,
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
        offered_rps=1,
        minimum_offered_rate_attainment=0.5,
        matched_load_relative_tolerance=0.1,
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
                offered_rps=1,
                minimum_offered_rate_attainment=0.5,
                matched_load_relative_tolerance=0.1,
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

    warmup_error = json.loads(json.dumps(terminals))
    warmup_error[0].update(
        {"outcome": "error", "status": 0, "oracle_valid": False, "observed_output": None}
    )
    rejected(records, warmup_error, ledger, "private_warmup_terminal")

    warmup_5xx = json.loads(json.dumps(terminals))
    warmup_5xx[0].update(
        {"outcome": "5xx", "status": 503, "oracle_valid": False, "observed_output": None}
    )
    rejected(records, warmup_5xx, ledger, "private_warmup_terminal")

    warmup_oracle_missing = json.loads(json.dumps(terminals))
    warmup_oracle_missing[0]["oracle_valid"] = False
    rejected(records, warmup_oracle_missing, ledger, "private_terminal_record_binding")

    warmup_oracle_forged = json.loads(json.dumps(terminals))
    warmup_oracle_forged[0]["observed_output"] = 0.75
    rejected(records, warmup_oracle_forged, ledger, "private_terminal_record_binding")

    missing_terminal = json.loads(json.dumps(terminals[1:]))
    rejected(records, missing_terminal, ledger, "private_terminal_identity_set")

    duplicate_warmup = json.loads(json.dumps(terminals))
    duplicate_warmup.append(json.loads(json.dumps(terminals[0])))
    rejected(records, duplicate_warmup, ledger, "private_terminal_record_binding")

    rejected_terminal = json.loads(json.dumps(terminals))
    rejected_terminal.append(terminal(ledger[2]))
    rejected(records, rejected_terminal, ledger, "private_terminal_record_binding")

    no_warmup_ledger = json.loads(json.dumps(ledger[1:]))
    for sequence, item in enumerate(no_warmup_ledger):
        item["global_sequence"] = sequence
        item["request_id"] = f"{attempt_id}-{sequence}"
    no_warmup_terminals = [terminal(no_warmup_ledger[0])]
    no_warmup_records = [
        {
            key: value
            for key, value in no_warmup_terminals[0].items()
            if key not in {"global_sequence", "phase"}
        }
    ]
    rejected(
        no_warmup_records,
        no_warmup_terminals,
        no_warmup_ledger,
        "private_warmup_offered",
    )

    with pytest.raises(X1ResumeTestbedError, match="private_warmup_offered"):
        x1_resume_module._validate_attempt_records(
            records,
            terminals,
            ledger,
            attempt_id=attempt_id,
            model_mix=model_mix,
            warmup_seconds=1,
            offered_rps=2,
            minimum_offered_rate_attainment=1.0,
            matched_load_relative_tolerance=0.0,
            measurement_start_ns=measurement_start_ns,
            measurement_end_ns=measurement_end_ns,
            admission=admission,
        )


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
    assert MODEL_DESCRIPTION in runbook
    for key in MODEL_CLAIM_CONTRACT:
        if key != "model_description":
            assert f"`{key}=false`" in runbook

    runner = (ROOT / "scripts/dev/run_s8_v4_x1_resume_testbed.py").read_text(encoding="utf-8")
    assert "_validate_attempt_records(" in runner
    assert '"oracle_valid": oracle_valid' in runner


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
        ("released_at", "2026-08-25T00:30:00.000000Z"),
        ("released_at", "2026-08-25T00:30:00+00:00"),
        ("acquired_at", "2026-08-25T00:00:00.000000Z"),
        ("released_at", "2026-08-24T23:59:59Z"),
        ("released_at", "2026-08-25T02:00:01Z"),
        ("expires_at", "2026-08-24T23:59:59Z"),
        ("fencing_token", "not-a-fencing-token"),
        ("source_commit", "c" * 40),
        ("owner_pid", "1234"),
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
    manifest = {"source_bindings": bindings}
    x1_resume_module.validate_governed_source_bindings(manifest, data_root=data_root, config=cfg)

    identity = x1_resume_module.GOVERNED_SOURCE_IDENTITIES["s3_logistic"]
    source_path = data_root / str(identity["path"])
    original_source = source_path.read_bytes()
    source_path.write_bytes(original_source + b"coherent replacement")
    coherent = json.loads(json.dumps(manifest))
    coherent_binding = coherent["source_bindings"]["higgs_logistic_regression"]
    coherent_binding["source_sha256"] = sha256_file(source_path)
    coherent_binding["source_bytes"] = source_path.stat().st_size
    with pytest.raises(X1ResumeTestbedError, match="x1_resume_governed_identity"):
        x1_resume_module.validate_governed_source_bindings(
            coherent, data_root=data_root, config=cfg
        )
    source_path.write_bytes(original_source)

    shape_drift = json.loads(json.dumps(manifest))
    shape_drift["source_bindings"]["higgs_logistic_regression"]["replay"]["replay_shape"] = [
        99999,
        28,
    ]
    with pytest.raises(X1ResumeTestbedError, match="x1_resume_governed_s3_binding"):
        x1_resume_module.validate_governed_source_bindings(
            shape_drift, data_root=data_root, config=cfg
        )
