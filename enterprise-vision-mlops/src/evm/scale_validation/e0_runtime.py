from __future__ import annotations

import hashlib
import json
import math
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence


SCHEMA_VERSION = "evm.s8_v4.e0_environment_experiment.v1"
ATTEMPT_SCHEMA_VERSION = "evm.s8_v4.e0_attempt_private.v1"
CLAIM_BOUNDARY = (
    "Controlled qualification on one Windows/WSL2 physical node and one RTX 4080; "
    "no production SLA, HA/DR, multi-node, multi-GPU, MIG, MPS, or CUDA-overlap claim."
)


class E0RuntimeError(RuntimeError):
    pass


def canonical(value: Any) -> str:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode("ascii")).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass(frozen=True)
class E0RuntimeConfig:
    path: Path
    raw_sha256: str
    schema_version: str
    triton_image: str
    triton_image_digest: str
    expected_gpu_name: str
    model_name: str
    model_version: str
    backend: str
    input_values: tuple[float, ...]
    expected_output: tuple[float, ...]
    repetitions: int
    readiness_timeout_seconds: float
    prometheus_timeout_seconds: float
    cleanup_timeout_seconds: float
    vram_tolerance_mib: float
    vram_tolerance_ratio: float
    http_port: int
    grpc_port: int
    metrics_port: int
    prometheus_job: str
    profiler_required_repetitions: int

    @classmethod
    def from_path(cls, path: Path) -> "E0RuntimeConfig":
        raw = path.read_bytes()
        canonical_raw = raw.replace(b"\r\n", b"\n")
        if b"\r" in canonical_raw:
            raise E0RuntimeError("e0_config_noncanonical_line_ending")
        payload = tomllib.loads(canonical_raw.decode("utf-8"))
        identity = dict(payload["identity"])
        procedure = dict(payload["procedure"])
        prediction = dict(payload["prediction"])
        ports = dict(payload["ports"])
        telemetry = dict(payload["telemetry"])
        config = cls(
            path=path,
            raw_sha256=hashlib.sha256(canonical_raw).hexdigest(),
            schema_version=str(payload["schema_version"]),
            triton_image=str(identity["triton_image"]),
            triton_image_digest=str(identity["triton_image_digest"]),
            expected_gpu_name=str(identity["expected_gpu_name"]),
            model_name=str(identity["model_name"]),
            model_version=str(identity["model_version"]),
            backend=str(identity["backend"]),
            input_values=tuple(float(value) for value in prediction["input_values"]),
            expected_output=tuple(float(value) for value in prediction["expected_output"]),
            repetitions=int(procedure["repetitions"]),
            readiness_timeout_seconds=float(procedure["readiness_timeout_seconds"]),
            prometheus_timeout_seconds=float(procedure["prometheus_timeout_seconds"]),
            cleanup_timeout_seconds=float(procedure["cleanup_timeout_seconds"]),
            vram_tolerance_mib=float(procedure["vram_tolerance_mib"]),
            vram_tolerance_ratio=float(procedure["vram_tolerance_ratio"]),
            http_port=int(ports["http"]),
            grpc_port=int(ports["grpc"]),
            metrics_port=int(ports["metrics"]),
            prometheus_job=str(telemetry["prometheus_job"]),
            profiler_required_repetitions=int(telemetry["profiler_required_repetitions"]),
        )
        config.assert_frozen()
        return config

    def assert_frozen(self) -> None:
        if self.schema_version != "evm.s8_v4.e0_runtime_config.v1":
            raise E0RuntimeError("e0_config_schema")
        if self.repetitions != 3:
            raise E0RuntimeError("e0_repetitions_must_equal_three")
        if self.readiness_timeout_seconds != 30:
            raise E0RuntimeError("e0_readiness_timeout_must_equal_30")
        if self.prometheus_timeout_seconds != 30:
            raise E0RuntimeError("e0_prometheus_timeout_must_equal_30")
        if self.cleanup_timeout_seconds != 120:
            raise E0RuntimeError("e0_cleanup_timeout_must_equal_120")
        if self.vram_tolerance_mib != 256 or self.vram_tolerance_ratio != 0.05:
            raise E0RuntimeError("e0_vram_tolerance_contract")
        if self.profiler_required_repetitions != 3:
            raise E0RuntimeError("e0_profiler_repetitions_must_equal_three")
        if len({self.http_port, self.grpc_port, self.metrics_port}) != 3:
            raise E0RuntimeError("e0_ports_must_be_distinct")
        if not self.triton_image_digest.startswith("sha256:"):
            raise E0RuntimeError("e0_image_digest_required")
        if len(self.input_values) != len(self.expected_output) or not self.input_values:
            raise E0RuntimeError("e0_prediction_shape")

    @property
    def immutable_image(self) -> str:
        repository = self.triton_image.rsplit(":", 1)[0]
        return f"{repository}@{self.triton_image_digest}"

    def public_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "config_sha256": self.raw_sha256,
            "triton_image": self.triton_image,
            "triton_image_digest": self.triton_image_digest,
            "expected_gpu_name": self.expected_gpu_name,
            "model_name": self.model_name,
            "model_version": self.model_version,
            "backend": self.backend,
            "repetitions": self.repetitions,
            "readiness_timeout_seconds": self.readiness_timeout_seconds,
            "prometheus_timeout_seconds": self.prometheus_timeout_seconds,
            "cleanup_timeout_seconds": self.cleanup_timeout_seconds,
            "vram_tolerance_mib": self.vram_tolerance_mib,
            "vram_tolerance_ratio": self.vram_tolerance_ratio,
            "ports": {
                "http": self.http_port,
                "grpc": self.grpc_port,
                "metrics": self.metrics_port,
            },
            "prometheus_job": self.prometheus_job,
            "profiler_required_repetitions": self.profiler_required_repetitions,
            "prediction_input_sha256": canonical_sha256(list(self.input_values)),
            "expected_output_sha256": canonical_sha256(list(self.expected_output)),
        }


def project_attempt(raw: Mapping[str, Any], config: E0RuntimeConfig) -> dict[str, Any]:
    errors: list[str] = []
    _validate_finite(raw, "attempt", errors)
    if raw.get("schema_version") != ATTEMPT_SCHEMA_VERSION:
        errors.append("schema_version")
    repetition = int(raw.get("repetition", 0))
    if repetition not in range(1, config.repetitions + 1):
        errors.append("repetition")

    environment = dict(raw.get("environment", {}))
    image = dict(raw.get("image", {}))
    model = dict(raw.get("model", {}))
    runtime = dict(raw.get("runtime", {}))
    inference = dict(raw.get("inference", {}))
    metrics = dict(raw.get("metrics", {}))
    profiler = dict(raw.get("profiler", {}))
    cleanup = dict(raw.get("cleanup", {}))

    host_gpu = dict(environment.get("host_gpu", {}))
    container_gpu = dict(environment.get("container_gpu", {}))
    if host_gpu.get("uuid") != container_gpu.get("uuid"):
        errors.append("gpu_uuid_mismatch")
    if host_gpu.get("name") != container_gpu.get("name"):
        errors.append("gpu_name_mismatch")
    if host_gpu.get("name") != config.expected_gpu_name:
        errors.append("gpu_name_contract")
    if image.get("repo_digest") != config.triton_image_digest:
        errors.append("triton_image_digest")
    if model.get("name") != config.model_name:
        errors.append("model_name")
    if model.get("version") != config.model_version:
        errors.append("model_version")
    if model.get("backend") != config.backend:
        errors.append("model_backend")
    for key in ("repository_sha256", "artifact_sha256", "config_sha256"):
        if not _is_sha256(model.get(key)):
            errors.append(f"model_{key}")

    ready_seconds = float(runtime.get("ready_seconds", math.inf))
    prom_seconds = float(metrics.get("prometheus_up_seconds", math.inf))
    identity_match = not any(
        item for item in errors if item.startswith(("gpu_", "triton_", "model_"))
    )
    ready_passed = (
        runtime.get("server_live") is True
        and runtime.get("server_ready") is True
        and runtime.get("model_ready") is True
        and ready_seconds <= config.readiness_timeout_seconds
    )
    observed_output = [float(value) for value in inference.get("output", [])]
    output_match = len(observed_output) == len(config.expected_output) and all(
        math.isclose(observed, expected, rel_tol=1e-6, abs_tol=1e-6)
        for observed, expected in zip(observed_output, config.expected_output, strict=True)
    )
    cuda_passed = (
        inference.get("transport_ok") is True
        and output_match
        and inference.get("gpu_instance_kind") == "KIND_GPU"
        and float(metrics.get("triton_success_count", 0)) >= 1
        and float(metrics.get("triton_compute_infer_count", 0)) >= 1
        and float(metrics.get("gpu_memory_used_bytes", 0)) > 0
        and inference.get("cpu_fallback_detected") is False
    )
    metrics_passed = (
        metrics.get("direct_endpoint_ok") is True
        and metrics.get("prometheus_target_up") is True
        and metrics.get("prometheus_model_metric_queryable") is True
        and prom_seconds <= config.prometheus_timeout_seconds
    )
    profiler_passed = (
        profiler.get("tool") in {"nsight-systems", "cupti"}
        and profiler.get("scope") == "same-container-cuda-profiler-qualification"
        and profiler.get("triton_inference_traced") is False
        and profiler.get("parseable") is True
        and int(profiler.get("cuda_kernel_count", 0)) > 0
        and _is_sha256(profiler.get("timeline_sha256"))
        and _is_sha256(profiler.get("source_sha256"))
        and _is_sha256(profiler.get("execution_log_sha256"))
    )
    vram_delta = abs(float(cleanup.get("vram_delta_mib", math.inf)))
    vram_tolerance = max(
        config.vram_tolerance_mib,
        float(cleanup.get("preflight_total_vram_mib", 0)) * config.vram_tolerance_ratio,
    )
    cleanup_passed = (
        cleanup.get("container_absent") is True
        and cleanup.get("port_listeners_absent") is True
        and cleanup.get("gpu_context_absent") is True
        and cleanup.get("lease_absent") is True
        and cleanup.get("prometheus_target_absent") is True
        and cleanup.get("temporary_kubernetes_resources_absent") is True
        and cleanup.get("queue_active_zero") is True
        and cleanup.get("queue_leased_zero") is True
        and cleanup.get("queue_outcome_unknown_zero") is True
        and cleanup.get("b0_ready") is True
        and cleanup.get("b0_cuda_inference") is True
        and float(cleanup.get("elapsed_seconds", math.inf)) <= config.cleanup_timeout_seconds
        and vram_delta <= vram_tolerance
    )
    checks = {
        "identity_match": identity_match,
        "readiness_within_30s": ready_passed,
        "cuda_batch_one": cuda_passed,
        "metrics_queryable_within_30s": metrics_passed,
        "profiler_timeline_parseable": profiler_passed,
        "cleanup_within_120s": cleanup_passed,
    }
    if errors:
        raise E0RuntimeError("e0_attempt_invalid:" + ",".join(sorted(set(errors))))
    return {
        "attempt_id": raw.get("attempt_id"),
        "repetition": repetition,
        "started_at": raw.get("started_at"),
        "finished_at": raw.get("finished_at"),
        "credit": raw.get("credit"),
        "triton_image_digest": image.get("repo_digest"),
        "gpu_uuid": host_gpu.get("uuid"),
        "gpu_name": host_gpu.get("name"),
        "model_repository_sha256": model.get("repository_sha256"),
        "model_artifact_sha256": model.get("artifact_sha256"),
        "model_config_sha256": model.get("config_sha256"),
        "ready_seconds": ready_seconds,
        "prometheus_up_seconds": prom_seconds,
        "output_sha256": canonical_sha256(observed_output),
        "triton_success_count": float(metrics.get("triton_success_count", 0)),
        "gpu_memory_used_bytes": float(metrics.get("gpu_memory_used_bytes", 0)),
        "profiler": {
            "tool": profiler.get("tool"),
            "version": profiler.get("version"),
            "scope": profiler.get("scope"),
            "triton_inference_traced": profiler.get("triton_inference_traced"),
            "cuda_kernel_count": int(profiler.get("cuda_kernel_count", 0)),
            "timeline_sha256": profiler.get("timeline_sha256"),
            "source_sha256": profiler.get("source_sha256"),
            "execution_log_sha256": profiler.get("execution_log_sha256"),
        },
        "cleanup": {
            "elapsed_seconds": float(cleanup.get("elapsed_seconds", math.inf)),
            "vram_delta_mib": float(cleanup.get("vram_delta_mib", math.inf)),
            "orphan_count": int(cleanup.get("orphan_count", -1)),
        },
        "checks": checks,
        "passed": all(checks.values()) and raw.get("credit") == "credit",
    }


def analyze_attempts(
    attempts: Sequence[Mapping[str, Any]], config: E0RuntimeConfig
) -> dict[str, Any]:
    expected_repetitions = set(range(1, config.repetitions + 1))
    actual_repetitions = {int(item.get("repetition", 0)) for item in attempts}
    identity_passes = sum(
        bool(dict(item.get("checks", {})).get("identity_match")) for item in attempts
    )
    inference_passes = sum(
        bool(dict(item.get("checks", {})).get("readiness_within_30s"))
        and bool(dict(item.get("checks", {})).get("cuda_batch_one"))
        for item in attempts
    )
    telemetry_passes = sum(
        bool(dict(item.get("checks", {})).get("metrics_queryable_within_30s"))
        and bool(dict(item.get("checks", {})).get("profiler_timeline_parseable"))
        for item in attempts
    )
    cleanup_passes = sum(
        bool(dict(item.get("checks", {})).get("cleanup_within_120s")) for item in attempts
    )
    matrix_complete = (
        len(attempts) == config.repetitions
        and actual_repetitions == expected_repetitions
        and len({item.get("attempt_id") for item in attempts}) == config.repetitions
        and all(item.get("passed") is True for item in attempts)
    )
    acceptance = {
        "E0-AC-01": matrix_complete and identity_passes == config.repetitions,
        "E0-AC-02": matrix_complete and inference_passes == config.repetitions,
        "E0-AC-03": matrix_complete and telemetry_passes >= config.profiler_required_repetitions,
        "E0-AC-04": matrix_complete and cleanup_passes == config.repetitions,
    }
    return {
        "repetitions_expected": config.repetitions,
        "repetitions_observed": len(attempts),
        "identity_passes": identity_passes,
        "inference_passes": inference_passes,
        "telemetry_passes": telemetry_passes,
        "cleanup_passes": cleanup_passes,
        "matrix_complete": matrix_complete,
        "acceptance": acceptance,
        "evidence_ready": all(acceptance.values()),
    }


def _validate_finite(value: Any, path: str, errors: list[str]) -> None:
    if isinstance(value, float) and not math.isfinite(value):
        errors.append(f"non_finite:{path}")
    elif isinstance(value, Mapping):
        for key, item in value.items():
            _validate_finite(item, f"{path}.{key}", errors)
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for index, item in enumerate(value):
            _validate_finite(item, f"{path}[{index}]", errors)


def _is_sha256(value: Any) -> bool:
    if not isinstance(value, str) or len(value) != 64:
        return False
    return all(character in "0123456789abcdef" for character in value)
