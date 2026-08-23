from __future__ import annotations

import hashlib
import json
import math
import subprocess
import time
import tomllib
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from evm.control_panel.scenario_workloads import (
    assert_scale_validation_gpu_lease_owner,
)
from evm.model_runtime.tiny_mlp import TINY_MLP_ARCHITECTURE, build_tiny_mlp
from evm.scale_validation.evidence import write_public_json


CLAIM_BOUNDARY = (
    "Measured HIGGS Tiny MLP batching trade-offs on one local physical node and "
    "one consumer GPU. No customer traffic, production SLA, physical multi-node "
    "or multi-zone HA, stateful HA/DR, multi-GPU, business A/B, or terabyte claim."
)


class S4RuntimeError(RuntimeError):
    pass


@dataclass(frozen=True)
class S4Point:
    batch_size: int
    max_delay_ms: int
    instance_count: int
    mode: str

    @property
    def point_id(self) -> str:
        return (
            f"{self.mode}-batch-{self.batch_size}-delay-{self.max_delay_ms}-"
            f"instances-{self.instance_count}"
        )


@dataclass(frozen=True)
class S4RuntimeConfig:
    path: Path
    sha256: str
    dataset_version: str
    seed: int
    s3_registry_path: Path
    train_features_path: Path
    train_labels_path: Path
    validation_features_path: Path
    validation_labels_path: Path
    replay_features_path: Path
    model_root: Path
    architecture: str
    dtype: str
    feature_count: int
    training_epochs: int
    training_batch_size: int
    learning_rate: float
    weight_decay: float
    max_train_rows: int
    max_validation_rows: int
    batch_sizes: tuple[int, ...]
    max_delays_ms: tuple[int, ...]
    baseline_instance_count: int
    instance_axis_counts: tuple[int, ...]
    closed_concurrency: int
    repetitions: int
    warmup_seconds: float
    measurement_seconds: float
    cooldown_seconds: float
    resource_sample_interval_seconds: float
    prometheus_scrape_interval_seconds: float
    max_outstanding: int
    max_outstanding_bytes: int
    max_request_bytes: int
    admission_wait_seconds: float
    request_timeout_seconds: float
    retry_after_seconds: int
    trace_flush_timeout_seconds: float
    trace_poll_interval_seconds: float
    open_service_rate_fraction: float
    open_maximum_target_rps: float
    open_repetitions: int
    maximum_error_rate: float
    maximum_p99_ms: float
    maximum_queue_wait_ms: float
    hard_stop_p99_ms: float
    hard_stop_queue_wait_ms: float
    maximum_temperature_celsius: float
    maximum_power_watts: float
    maximum_api_process_tree_rss_bytes: int
    queue_drain_timeout_seconds: float
    require_zero_oom: bool
    maximum_queue_wait_seconds: float
    capacity_safety_factor: float
    prior_depth: int
    rollback_depth: int
    maximum_depth: int
    allow_automatic_increase: bool
    preparation_closed_concurrency: int
    preparation_warmup_seconds: float
    preparation_measurement_seconds: float
    preparation_cooldown_seconds: float

    @classmethod
    def from_path(cls, path: Path, *, data_root: Path) -> "S4RuntimeConfig":
        resolved = path.resolve()
        with resolved.open("rb") as handle:
            payload = tomllib.load(handle)
        if payload.get("schema_version") != "evm.s4_gpu_batching_runtime.v1":
            raise S4RuntimeError("s4_runtime_config_schema_invalid")
        paths = _section(payload, "paths")
        model = _section(payload, "model")
        training = _section(payload, "training")
        inference = _section(payload, "inference")
        opened = _section(payload, "open_loop")
        guardrails = _section(payload, "guardrails")
        capacity = _section(payload, "capacity_recalculation")
        preparation = _section(payload, "preparation")
        observability = _section(payload, "observability")
        config = cls(
            path=resolved,
            sha256=file_sha256(resolved),
            dataset_version=str(payload.get("dataset_version") or ""),
            seed=int(payload.get("seed", 0)),
            s3_registry_path=data_root / str(paths["s3_registry_relative_path"]),
            train_features_path=data_root / str(paths["train_features_relative_path"]),
            train_labels_path=data_root / str(paths["train_labels_relative_path"]),
            validation_features_path=data_root / str(paths["validation_features_relative_path"]),
            validation_labels_path=data_root / str(paths["validation_labels_relative_path"]),
            replay_features_path=data_root / str(paths["replay_features_relative_path"]),
            model_root=data_root / str(paths["model_root_relative_path"]),
            architecture=str(model["architecture"]),
            dtype=str(model["dtype"]),
            feature_count=int(model["feature_count"]),
            training_epochs=int(training["epochs"]),
            training_batch_size=int(training["batch_size"]),
            learning_rate=float(training["learning_rate"]),
            weight_decay=float(training["weight_decay"]),
            max_train_rows=int(training["max_train_rows"]),
            max_validation_rows=int(training["max_validation_rows"]),
            batch_sizes=tuple(int(value) for value in inference["batch_sizes"]),
            max_delays_ms=tuple(int(value) for value in inference["max_delays_ms"]),
            baseline_instance_count=int(inference["baseline_instance_count"]),
            instance_axis_counts=tuple(int(value) for value in inference["instance_axis_counts"]),
            closed_concurrency=int(inference["closed_concurrency"]),
            repetitions=int(inference["repetitions"]),
            warmup_seconds=float(inference["warmup_seconds"]),
            measurement_seconds=float(inference["measurement_seconds"]),
            cooldown_seconds=float(inference["cooldown_seconds"]),
            resource_sample_interval_seconds=float(inference["resource_sample_interval_seconds"]),
            prometheus_scrape_interval_seconds=float(
                inference["prometheus_scrape_interval_seconds"]
            ),
            max_outstanding=int(inference["max_outstanding"]),
            max_outstanding_bytes=int(inference["max_outstanding_bytes"]),
            max_request_bytes=int(inference["max_request_bytes"]),
            admission_wait_seconds=float(inference["admission_wait_seconds"]),
            request_timeout_seconds=float(inference["request_timeout_seconds"]),
            retry_after_seconds=int(inference["retry_after_seconds"]),
            trace_flush_timeout_seconds=float(observability["trace_flush_timeout_seconds"]),
            trace_poll_interval_seconds=float(observability["trace_poll_interval_seconds"]),
            open_service_rate_fraction=float(opened["service_rate_fraction"]),
            open_maximum_target_rps=float(opened["maximum_target_requests_per_second"]),
            open_repetitions=int(opened["repetitions"]),
            maximum_error_rate=float(guardrails["maximum_error_rate"]),
            maximum_p99_ms=float(guardrails["maximum_p99_ms"]),
            maximum_queue_wait_ms=float(guardrails["maximum_queue_wait_ms"]),
            hard_stop_p99_ms=float(guardrails["hard_stop_p99_ms"]),
            hard_stop_queue_wait_ms=float(guardrails["hard_stop_queue_wait_ms"]),
            maximum_temperature_celsius=float(guardrails["maximum_temperature_celsius"]),
            maximum_power_watts=float(guardrails["maximum_power_watts"]),
            maximum_api_process_tree_rss_bytes=int(
                guardrails["maximum_api_process_tree_rss_bytes"]
            ),
            queue_drain_timeout_seconds=float(guardrails["queue_drain_timeout_seconds"]),
            require_zero_oom=bool(guardrails["require_zero_oom"]),
            maximum_queue_wait_seconds=float(capacity["maximum_queue_wait_seconds"]),
            capacity_safety_factor=float(capacity["safety_factor"]),
            prior_depth=int(capacity["prior_depth"]),
            rollback_depth=int(capacity["rollback_depth"]),
            maximum_depth=int(capacity["maximum_depth"]),
            allow_automatic_increase=bool(capacity["allow_automatic_increase"]),
            preparation_closed_concurrency=int(preparation["closed_concurrency"]),
            preparation_warmup_seconds=float(preparation["warmup_seconds"]),
            preparation_measurement_seconds=float(preparation["measurement_seconds"]),
            preparation_cooldown_seconds=float(preparation["cooldown_seconds"]),
        )
        config.validate()
        return config

    def validate(self) -> None:
        if self.dataset_version != "uci-higgs-2014-s3-v1" or self.seed <= 0:
            raise S4RuntimeError("s4_dataset_identity_invalid")
        if (
            self.architecture != TINY_MLP_ARCHITECTURE
            or self.dtype != "float32"
            or self.feature_count != 28
        ):
            raise S4RuntimeError("s4_model_contract_invalid")
        if self.batch_sizes != (1, 4, 8, 16, 32):
            raise S4RuntimeError("s4_batch_matrix_invalid")
        if self.max_delays_ms != (0, 2, 5, 10):
            raise S4RuntimeError("s4_delay_matrix_invalid")
        if self.instance_axis_counts != (1, 2) or self.baseline_instance_count != 1:
            raise S4RuntimeError("s4_instance_axis_invalid")
        if self.repetitions != 3 or self.open_repetitions != 3:
            raise S4RuntimeError("s4_requires_three_independent_repetitions")
        if self.closed_concurrency != 64:
            raise S4RuntimeError("s4_closed_concurrency_not_frozen")
        if (
            self.preparation_closed_concurrency,
            self.preparation_warmup_seconds,
            self.preparation_measurement_seconds,
            self.preparation_cooldown_seconds,
        ) != (1, 2.0, 5.0, 1.0):
            raise S4RuntimeError("s4_preparation_profile_invalid")
        positive = (
            self.training_epochs,
            self.training_batch_size,
            self.max_train_rows,
            self.max_validation_rows,
            self.closed_concurrency,
            self.max_outstanding,
            self.max_outstanding_bytes,
            self.max_request_bytes,
            self.retry_after_seconds,
            self.prior_depth,
            self.rollback_depth,
            self.maximum_depth,
        )
        if min(positive) <= 0:
            raise S4RuntimeError("s4_positive_bound_invalid")
        if self.open_service_rate_fraction != 0.70:
            raise S4RuntimeError("s4_open_rate_fraction_not_frozen")
        if self.open_maximum_target_rps != 80.0:
            raise S4RuntimeError("s4_open_rate_ceiling_not_frozen")
        if not 0 < self.capacity_safety_factor <= 1:
            raise S4RuntimeError("s4_capacity_safety_factor_invalid")
        if not (
            self.trace_flush_timeout_seconds > 0
            and 0 < self.trace_poll_interval_seconds <= self.trace_flush_timeout_seconds
        ):
            raise S4RuntimeError("s4_trace_flush_contract_invalid")
        if self.max_outstanding < max(self.batch_sizes) * max(self.instance_axis_counts):
            raise S4RuntimeError("s4_outstanding_capacity_invalid")
        if self.max_outstanding_bytes < self.max_request_bytes:
            raise S4RuntimeError("s4_outstanding_bytes_invalid")
        if not (
            self.hard_stop_p99_ms > self.maximum_p99_ms
            and self.hard_stop_queue_wait_ms > self.maximum_queue_wait_ms
            and self.hard_stop_p99_ms < self.request_timeout_seconds * 1000
            and self.hard_stop_queue_wait_ms < self.request_timeout_seconds * 1000
        ):
            raise S4RuntimeError("s4_latency_guardrail_layers_invalid")
        for required in (
            self.s3_registry_path,
            self.train_features_path,
            self.train_labels_path,
            self.validation_features_path,
            self.validation_labels_path,
            self.replay_features_path,
        ):
            if not required.is_file():
                raise S4RuntimeError(f"s4_required_input_missing:{required.name}")

    def assert_frozen(self) -> None:
        if file_sha256(self.path) != self.sha256:
            raise S4RuntimeError("s4_runtime_config_changed")

    def matrix_points(self) -> list[S4Point]:
        return [
            S4Point(batch, delay, self.baseline_instance_count, "matrix")
            for batch in self.batch_sizes
            for delay in self.max_delays_ms
        ]

    def instance_point(self) -> S4Point:
        return S4Point(1, 0, 2, "instance-axis")

    def public_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "evm.s4_gpu_batching_runtime.v1",
            "sha256": self.sha256,
            "dataset_version": self.dataset_version,
            "seed": self.seed,
            "architecture": self.architecture,
            "dtype": self.dtype,
            "batch_sizes": list(self.batch_sizes),
            "max_delays_ms": list(self.max_delays_ms),
            "instance_axis_counts": list(self.instance_axis_counts),
            "closed_concurrency": self.closed_concurrency,
            "repetitions": self.repetitions,
            "warmup_seconds": self.warmup_seconds,
            "measurement_seconds": self.measurement_seconds,
            "cooldown_seconds": self.cooldown_seconds,
            "resource_sample_interval_seconds": self.resource_sample_interval_seconds,
            "prometheus_scrape_interval_seconds": self.prometheus_scrape_interval_seconds,
            "observability": {
                "trace_flush_timeout_seconds": self.trace_flush_timeout_seconds,
                "trace_poll_interval_seconds": self.trace_poll_interval_seconds,
            },
            "open_loop": {
                "service_rate_fraction": self.open_service_rate_fraction,
                "maximum_target_requests_per_second": self.open_maximum_target_rps,
                "repetitions": self.open_repetitions,
                "selection_reason": (
                    "Use the lower of thirty-percent saturation headroom and the "
                    "three-repeat calibrated 80 RPS ceiling before applying the fixed "
                    "operating latency SLO."
                ),
            },
            "preparation": {
                "closed_concurrency": self.preparation_closed_concurrency,
                "warmup_seconds": self.preparation_warmup_seconds,
                "measurement_seconds": self.preparation_measurement_seconds,
                "cooldown_seconds": self.preparation_cooldown_seconds,
            },
            "guardrails": {
                "maximum_error_rate": self.maximum_error_rate,
                "maximum_p99_ms": self.maximum_p99_ms,
                "maximum_queue_wait_ms": self.maximum_queue_wait_ms,
                "hard_stop_p99_ms": self.hard_stop_p99_ms,
                "hard_stop_queue_wait_ms": self.hard_stop_queue_wait_ms,
                "maximum_temperature_celsius": self.maximum_temperature_celsius,
                "maximum_power_watts": self.maximum_power_watts,
                "require_zero_oom": self.require_zero_oom,
            },
            "capacity_recalculation": {
                "maximum_queue_wait_seconds": self.maximum_queue_wait_seconds,
                "safety_factor": self.capacity_safety_factor,
                "prior_depth": self.prior_depth,
                "rollback_depth": self.rollback_depth,
                "maximum_depth": self.maximum_depth,
                "allow_automatic_increase": self.allow_automatic_increase,
            },
        }


def train_tiny_mlp(
    config: S4RuntimeConfig,
    *,
    source_revision: str,
    lease_run_id: str,
    lease_id: str,
    fencing_token: str,
) -> dict[str, Any]:
    assert_scale_validation_gpu_lease_owner(
        run_id=lease_run_id,
        lease_id=lease_id,
        fencing_token=fencing_token,
        purpose="scale_validation_training",
    )
    try:
        import numpy as np
        import torch
    except ImportError as exc:
        raise S4RuntimeError("s4_training_runtime_dependency_missing") from exc
    if not torch.cuda.is_available() or torch.cuda.device_count() != 1:
        raise S4RuntimeError("s4_training_cuda_identity_invalid")
    config.assert_frozen()
    torch.manual_seed(config.seed)
    torch.cuda.manual_seed_all(config.seed)
    np.random.seed(config.seed)
    torch.use_deterministic_algorithms(True)
    features = np.load(config.train_features_path, mmap_mode="r")[: config.max_train_rows]
    labels = np.load(config.train_labels_path, mmap_mode="r")[: config.max_train_rows]
    validation_features = np.load(config.validation_features_path, mmap_mode="r")[
        : config.max_validation_rows
    ]
    validation_labels = np.load(config.validation_labels_path, mmap_mode="r")[
        : config.max_validation_rows
    ]
    mean = np.asarray(features, dtype=np.float64).mean(axis=0).astype("float32")
    scale = np.asarray(features, dtype=np.float64).std(axis=0).astype("float32")
    scale[scale < 1e-6] = 1.0
    device = torch.device("cuda:0")
    model = build_tiny_mlp(torch).to(device=device, dtype=torch.float32)
    optimizer = torch.optim.AdamW(
        model.parameters(), lr=config.learning_rate, weight_decay=config.weight_decay
    )
    criterion = torch.nn.BCEWithLogitsLoss()
    torch.cuda.reset_peak_memory_stats(device)
    started = time.perf_counter()
    losses: list[float] = []
    for _epoch in range(config.training_epochs):
        permutation = np.random.permutation(len(features))
        epoch_loss = 0.0
        batches = 0
        model.train()
        for offset in range(0, len(permutation), config.training_batch_size):
            indexes = permutation[offset : offset + config.training_batch_size]
            batch_features = (np.asarray(features[indexes], dtype="float32") - mean) / scale
            batch_labels = np.asarray(labels[indexes], dtype="float32")
            inputs = torch.from_numpy(batch_features).to(device)
            targets = torch.from_numpy(batch_labels).to(device)
            optimizer.zero_grad(set_to_none=True)
            loss = criterion(model(inputs), targets)
            loss.backward()
            optimizer.step()
            epoch_loss += float(loss.detach().cpu())
            batches += 1
        losses.append(epoch_loss / max(1, batches))
    torch.cuda.synchronize(device)
    training_seconds = time.perf_counter() - started
    model.eval()
    correct = 0
    observed = 0
    with torch.inference_mode():
        for offset in range(0, len(validation_features), config.training_batch_size):
            values = np.asarray(
                validation_features[offset : offset + config.training_batch_size],
                dtype="float32",
            )
            targets = np.asarray(
                validation_labels[offset : offset + config.training_batch_size],
                dtype="int64",
            )
            inputs = torch.from_numpy((values - mean) / scale).to(device)
            predictions = (torch.sigmoid(model(inputs)) >= 0.5).to("cpu").numpy()
            correct += int((predictions == targets).sum())
            observed += len(targets)
    registry_s3 = json.loads(config.s3_registry_path.read_text(encoding="utf-8"))
    output_root = config.model_root
    output_root.mkdir(parents=True, exist_ok=True)
    artifact_path = output_root / "tiny-mlp.pt"
    torch.save(
        {
            "state_dict": model.to("cpu").state_dict(),
            "architecture": config.architecture,
            "dtype": config.dtype,
            "dataset_identity_sha256": registry_s3["dataset_identity_sha256"],
            "split_manifest_sha256": registry_s3["split_manifest_sha256"],
            "source_revision": source_revision,
        },
        artifact_path,
    )
    artifact_sha = file_sha256(artifact_path)
    preprocessing = {
        "kind": "standardize",
        "mean": [float(value) for value in mean],
        "scale": [float(value) for value in scale],
    }
    preprocessing_sha = canonical_sha256(preprocessing)
    identity_material = {
        "dataset_identity_sha256": registry_s3["dataset_identity_sha256"],
        "split_manifest_sha256": registry_s3["split_manifest_sha256"],
        "artifact_sha256": artifact_sha,
        "architecture": config.architecture,
        "dtype": config.dtype,
        "preprocessing_sha256": preprocessing_sha,
        "source_revision": source_revision,
    }
    model_identity = canonical_sha256(identity_material)
    registry_path = output_root / "registry.json"
    registry = {
        "schema_version": "evm.s4_gpu_batch_registry.v1",
        "generated_at": utc_now(),
        "dataset_id": "uci-higgs",
        "dataset_version": config.dataset_version,
        "dataset_identity_sha256": registry_s3["dataset_identity_sha256"],
        "split_manifest_sha256": registry_s3["split_manifest_sha256"],
        "model_identity_sha256": model_identity,
        "artifact_uri": artifact_path.name,
        "artifact_sha256": artifact_sha,
        "architecture": config.architecture,
        "dtype": config.dtype,
        "preprocessing": preprocessing,
        "preprocessing_sha256": preprocessing_sha,
        "framework": {
            "torch": str(torch.__version__),
            "cuda_runtime": str(torch.version.cuda),
            "cudnn": str(torch.backends.cudnn.version()),
        },
        "source_revision": source_revision,
        "training": {
            "seed": config.seed,
            "epochs": config.training_epochs,
            "batch_size": config.training_batch_size,
            "train_rows": len(features),
            "validation_rows": observed,
            "final_loss": losses[-1],
            "validation_accuracy": correct / max(1, observed),
            "training_seconds": training_seconds,
            "peak_vram_bytes": int(torch.cuda.max_memory_allocated(device)),
        },
    }
    write_public_json(registry_path, registry)
    return {
        "registry_path": str(registry_path),
        "registry_sha256": file_sha256(registry_path),
        "artifact_path": str(artifact_path),
        "artifact_sha256": artifact_sha,
        "model_identity_sha256": model_identity,
        "dataset_identity_sha256": registry_s3["dataset_identity_sha256"],
        "split_manifest_sha256": registry_s3["split_manifest_sha256"],
        "training": registry["training"],
        "framework": registry["framework"],
    }


def analyze_s4_results(results: list[dict[str, Any]], config: S4RuntimeConfig) -> dict[str, Any]:
    expected_matrix = {
        (point.batch_size, point.max_delay_ms, point.instance_count)
        for point in config.matrix_points()
    }
    matrix = [item for item in results if item.get("mode") == "matrix"]
    instance_axis = [item for item in results if item.get("mode") == "instance-axis"]
    open_loop = [item for item in results if item.get("mode") == "open-loop"]
    observed_matrix = {
        (int(item["batch_size"]), int(item["max_delay_ms"]), int(item["instance_count"]))
        for item in matrix
    }
    repetition_counts = {
        key: sum(
            1
            for item in matrix
            if (
                int(item["batch_size"]),
                int(item["max_delay_ms"]),
                int(item["instance_count"]),
            )
            == key
        )
        for key in observed_matrix
    }
    matrix_complete = observed_matrix == expected_matrix and all(
        count == config.repetitions for count in repetition_counts.values()
    )
    aggregates = _aggregate_points(matrix)
    saturation_eligible = [
        item
        for item in aggregates
        if item["evidence_valid_all"]
        and item["error_rate_max"] <= config.maximum_error_rate
        and item["oom_count"] == 0
        and item["temperature_celsius_max"] <= config.maximum_temperature_celsius
        and item["power_watts_max"] <= config.maximum_power_watts
    ]
    selected = max(
        saturation_eligible,
        key=lambda item: (
            item["service_rps_mean"],
            -item["p99_ms_mean"],
            -item["peak_vram_bytes_max"],
        ),
        default=None,
    )
    throughput_p99_pareto = _pareto(aggregates, "p99_ms_mean")
    throughput_vram_pareto = _pareto(aggregates, "peak_vram_bytes_max")
    baseline_instance = next(
        (
            item
            for item in aggregates
            if item["batch_size"] == 1 and item["max_delay_ms"] == 0 and item["instance_count"] == 1
        ),
        None,
    )
    instance_two = _aggregate_points(instance_axis)
    instance_two_point = instance_two[0] if len(instance_two) == 1 else None
    instance_effect = None
    if baseline_instance and instance_two_point:
        instance_effect = {
            "batch_size": 1,
            "max_delay_ms": 0,
            "instance_1_service_rps": baseline_instance["service_rps_mean"],
            "instance_2_service_rps": instance_two_point["service_rps_mean"],
            "throughput_delta_percent": _percent_delta(
                instance_two_point["service_rps_mean"],
                baseline_instance["service_rps_mean"],
            ),
            "instance_1_p99_ms": baseline_instance["p99_ms_mean"],
            "instance_2_p99_ms": instance_two_point["p99_ms_mean"],
            "p99_delta_percent": _percent_delta(
                instance_two_point["p99_ms_mean"], baseline_instance["p99_ms_mean"]
            ),
        }
    open_loop_matches_selected = bool(
        selected
        and all(
            int(item.get("batch_size", -1)) == int(selected["batch_size"])
            and int(item.get("max_delay_ms", -1)) == int(selected["max_delay_ms"])
            and int(item.get("instance_count", -1)) == int(selected["instance_count"])
            for item in open_loop
        )
    )
    open_loop_valid = (
        len(open_loop) == config.open_repetitions
        and open_loop_matches_selected
        and all(item.get("evidence_valid") is True for item in open_loop)
        and all(item.get("load_generator_valid") is True for item in open_loop)
        and all(item.get("operating_guardrail_passed") is True for item in open_loop)
    )
    selected_safe = selected is not None and selected["oom_count"] == 0
    saturation_rate = float(selected["service_rps_mean"]) if selected else 0.0
    validated_service_rate = (
        _mean(open_loop, "service_rps") if open_loop_valid else 0.0
    )
    calculated_depth = min(
        config.maximum_depth,
        max(
            1,
            math.floor(
                validated_service_rate
                * config.maximum_queue_wait_seconds
                * config.capacity_safety_factor
            ),
        ),
    )
    applied_depth = (
        calculated_depth
        if config.allow_automatic_increase or calculated_depth <= config.prior_depth
        else config.prior_depth
    )
    capacity = {
        "selected_saturation_rate_requests_per_second": saturation_rate,
        "validated_open_loop_service_rate_requests_per_second": validated_service_rate,
        "maximum_queue_wait_seconds": config.maximum_queue_wait_seconds,
        "safety_factor": config.capacity_safety_factor,
        "formula": "floor(service_rate * maximum_queue_wait_seconds * safety_factor)",
        "calculated_depth": calculated_depth,
        "prior_depth": config.prior_depth,
        "applied_depth": applied_depth,
        "rollback_depth": config.rollback_depth,
        "automatic_increase_allowed": config.allow_automatic_increase,
    }
    acceptance = {
        "S4-AC-01": bool(matrix_complete and throughput_p99_pareto and throughput_vram_pareto),
        "S4-AC-02": bool(selected_safe and open_loop_valid),
        "S4-AC-03": bool(
            instance_effect is not None
            and len(instance_axis) == config.repetitions
            and all(item.get("evidence_valid") is True for item in instance_axis)
        ),
        "S4-AC-04": bool(open_loop_valid and validated_service_rate > 0 and calculated_depth > 0),
    }
    return {
        "acceptance": acceptance,
        "runtime_verdict": "passed" if all(acceptance.values()) else "failed",
        "matrix_complete": matrix_complete,
        "matrix_repetition_count": len(matrix),
        "instance_repetition_count": len(instance_axis),
        "open_loop_repetition_count": len(open_loop),
        "selection_contract": {
            "saturation_candidate_count": len(saturation_eligible),
            "matrix_operating_guardrail_pass_count": sum(
                1 for item in aggregates if item["operating_guardrail_passed_all"]
            ),
            "closed_loop_role": "capacity_and_pareto_candidate_discovery",
            "open_loop_role": "sustainable_operating_guardrail_validation",
            "open_loop_service_rate_fraction": config.open_service_rate_fraction,
            "open_loop_maximum_target_requests_per_second": config.open_maximum_target_rps,
            "open_loop_matches_selected": open_loop_matches_selected,
        },
        "aggregated_points": aggregates,
        "throughput_p99_pareto": throughput_p99_pareto,
        "throughput_peak_vram_pareto": throughput_vram_pareto,
        "selected_operating_point": selected,
        "instance_effect": instance_effect,
        "s2_capacity_recalculation": capacity,
    }


def _aggregate_points(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[int, int, int], list[dict[str, Any]]] = {}
    for item in results:
        key = (
            int(item["batch_size"]),
            int(item["max_delay_ms"]),
            int(item["instance_count"]),
        )
        grouped.setdefault(key, []).append(item)
    aggregates = []
    for (batch, delay, instances), items in sorted(grouped.items()):
        aggregates.append(
            {
                "batch_size": batch,
                "max_delay_ms": delay,
                "instance_count": instances,
                "repetitions": len(items),
                "service_rps_mean": _mean(items, "service_rps"),
                "p95_ms_mean": _mean(items, "p95_ms"),
                "p99_ms_mean": _mean(items, "p99_ms"),
                "p99_ms_max": _maximum(items, "p99_ms"),
                "error_rate_max": _maximum(items, "error_rate"),
                "queue_wait_p99_ms_max": _maximum(items, "queue_wait_p99_ms"),
                "peak_vram_bytes_max": int(_maximum(items, "peak_vram_bytes")),
                "gpu_utilization_percent_mean": _mean(items, "gpu_utilization_percent_mean"),
                "temperature_celsius_max": _maximum(items, "temperature_celsius_max"),
                "power_watts_max": _maximum(items, "power_watts_max"),
                "formed_batch_size_mean": _mean(items, "formed_batch_size_mean"),
                "fill_ratio_mean": _mean(items, "fill_ratio_mean"),
                "oom_count": int(sum(int(item.get("oom_count", 0)) for item in items)),
                "evidence_valid_all": all(item.get("evidence_valid") is True for item in items),
                "operating_guardrail_passed_all": all(
                    item.get("operating_guardrail_passed") is True for item in items
                ),
            }
        )
    return aggregates


def _pareto(points: list[dict[str, Any]], cost_key: str) -> list[dict[str, Any]]:
    result = []
    for candidate in points:
        dominated = any(
            other is not candidate
            and other["service_rps_mean"] >= candidate["service_rps_mean"]
            and other[cost_key] <= candidate[cost_key]
            and (
                other["service_rps_mean"] > candidate["service_rps_mean"]
                or other[cost_key] < candidate[cost_key]
            )
            for other in points
        )
        if not dominated:
            result.append(
                {
                    "batch_size": candidate["batch_size"],
                    "max_delay_ms": candidate["max_delay_ms"],
                    "instance_count": candidate["instance_count"],
                    "service_rps": candidate["service_rps_mean"],
                    cost_key: candidate[cost_key],
                }
            )
    return sorted(result, key=lambda item: item["service_rps"])


def _mean(items: list[dict[str, Any]], key: str) -> float:
    values = [_finite_float(item.get(key), key) for item in items]
    return sum(values) / len(values)


def _maximum(items: list[dict[str, Any]], key: str) -> float:
    return max(_finite_float(item.get(key), key) for item in items)


def _finite_float(value: object, label: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise S4RuntimeError(f"s4_result_metric_invalid:{label}") from exc
    if not math.isfinite(result) or result < 0:
        raise S4RuntimeError(f"s4_result_metric_invalid:{label}")
    return result


def _percent_delta(value: float, baseline: float) -> float:
    return 0.0 if baseline == 0 else ((value - baseline) / baseline) * 100


def source_identity(root: Path) -> tuple[str, str]:
    revision = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
        timeout=15,
    ).stdout.strip()
    branch = subprocess.run(
        ["git", "branch", "--show-current"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
        timeout=15,
    ).stdout.strip()
    if len(revision) != 40 or branch != "codex/distributed-scale-validation-plan":
        raise S4RuntimeError("s4_source_identity_invalid")
    if (
        subprocess.run(["git", "diff", "--quiet"], cwd=root, timeout=15, check=False).returncode
        != 0
        or subprocess.run(
            ["git", "diff", "--cached", "--quiet"], cwd=root, timeout=15, check=False
        ).returncode
        != 0
    ):
        raise S4RuntimeError("s4_tracked_worktree_must_be_clean")
    return revision, branch


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_sha256(payload: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()


def utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _section(payload: dict[str, Any], name: str) -> dict[str, Any]:
    value = payload.get(name)
    if not isinstance(value, dict):
        raise S4RuntimeError(f"s4_runtime_config_section_missing:{name}")
    return value
