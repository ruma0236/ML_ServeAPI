from __future__ import annotations

import hashlib
import json
import math
import statistics
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence


CLAIM_BOUNDARY = (
    "Controlled API rolling continuity and single-GPU handoff measurements on one "
    "local physical node. This is not customer traffic, a production SLA, physical-"
    "node or multi-zone HA/DR, zero-downtime GPU HA, or multi-GPU evidence."
)


class S6RuntimeError(RuntimeError):
    pass


@dataclass(frozen=True)
class ApiRuntimeConfig:
    namespace: str
    deployment: str
    service: str
    node_port: int
    replicas: int
    repetitions: int
    target_requests_per_second: float
    warmup_seconds: float
    measurement_seconds: float
    cooldown_seconds: float
    processing_delay_ms: int
    request_timeout_seconds: float
    maximum_attempts_per_logical_request: int
    retry_backoff_seconds: float
    rollout_offset_seconds: float
    runtime_poll_interval_seconds: float
    trace_sample_every: int
    trace_flush_timeout_seconds: float


@dataclass(frozen=True)
class RollingConfig:
    max_unavailable: int
    max_surge: int
    minimum_ready_seconds: int
    drain_timeout_seconds: float
    termination_grace_period_seconds: int
    rollout_timeout_seconds: float


@dataclass(frozen=True)
class GpuHandoffConfig:
    repetitions: int
    source_namespace: str
    source_deployment: str
    source_endpoint: str
    target_namespace: str
    target_deployment: str
    target_endpoint: str
    readiness_timeout_seconds: float
    request_timeout_seconds: float
    cooldown_seconds: float
    maximum_interruption_seconds: float
    runtime_poll_interval_seconds: float
    calibration_inference_requests: int
    acceptance_inference_requests: int
    sample_image_uri: str
    candidate_readiness_path: Path
    candidate_summary_path: Path
    candidate_release_submission_path: Path
    candidate_model_path: Path


@dataclass(frozen=True)
class GuardrailConfig:
    accepted_loss: int
    duplicate_effects: int
    maximum_error_rate: float
    maximum_p99_ms: float
    require_exact_rollback_identity: bool
    require_zero_gpu_owner_overlap: bool


@dataclass(frozen=True)
class S6RuntimeConfig:
    path: Path
    sha256: str
    seed: int
    api: ApiRuntimeConfig
    rolling: RollingConfig
    gpu_handoff: GpuHandoffConfig
    guardrails: GuardrailConfig
    claim_boundary: str

    @classmethod
    def from_path(cls, path: Path, *, data_root: Path) -> "S6RuntimeConfig":
        resolved = path.resolve()
        with resolved.open("rb") as handle:
            payload = tomllib.load(handle)
        if payload.get("schema_version") != "evm.s6_rolling_handoff_runtime.v1":
            raise S6RuntimeError("s6_runtime_config_schema_invalid")
        api = _section(payload, "api")
        rolling = _section(payload, "rolling")
        gpu = _section(payload, "gpu_handoff")
        guardrails = _section(payload, "guardrails")
        claim = _section(payload, "claim_boundary")
        config = cls(
            path=resolved,
            sha256=file_sha256(resolved),
            seed=int(payload.get("seed", 0)),
            api=ApiRuntimeConfig(
                namespace=str(api["namespace"]),
                deployment=str(api["deployment"]),
                service=str(api["service"]),
                node_port=int(api["node_port"]),
                replicas=int(api["replicas"]),
                repetitions=int(api["repetitions"]),
                target_requests_per_second=float(api["target_requests_per_second"]),
                warmup_seconds=float(api["warmup_seconds"]),
                measurement_seconds=float(api["measurement_seconds"]),
                cooldown_seconds=float(api["cooldown_seconds"]),
                processing_delay_ms=int(api["processing_delay_ms"]),
                request_timeout_seconds=float(api["request_timeout_seconds"]),
                maximum_attempts_per_logical_request=int(
                    api["maximum_attempts_per_logical_request"]
                ),
                retry_backoff_seconds=float(api["retry_backoff_seconds"]),
                rollout_offset_seconds=float(api["rollout_offset_seconds"]),
                runtime_poll_interval_seconds=float(
                    api["runtime_poll_interval_seconds"]
                ),
                trace_sample_every=int(api["trace_sample_every"]),
                trace_flush_timeout_seconds=float(api["trace_flush_timeout_seconds"]),
            ),
            rolling=RollingConfig(
                max_unavailable=int(rolling["max_unavailable"]),
                max_surge=int(rolling["max_surge"]),
                minimum_ready_seconds=int(rolling["minimum_ready_seconds"]),
                drain_timeout_seconds=float(rolling["drain_timeout_seconds"]),
                termination_grace_period_seconds=int(
                    rolling["termination_grace_period_seconds"]
                ),
                rollout_timeout_seconds=float(rolling["rollout_timeout_seconds"]),
            ),
            gpu_handoff=GpuHandoffConfig(
                repetitions=int(gpu["repetitions"]),
                source_namespace=str(gpu["source_namespace"]),
                source_deployment=str(gpu["source_deployment"]),
                source_endpoint=str(gpu["source_endpoint"]),
                target_namespace=str(gpu["target_namespace"]),
                target_deployment=str(gpu["target_deployment"]),
                target_endpoint=str(gpu["target_endpoint"]),
                readiness_timeout_seconds=float(gpu["readiness_timeout_seconds"]),
                request_timeout_seconds=float(gpu["request_timeout_seconds"]),
                cooldown_seconds=float(gpu["cooldown_seconds"]),
                maximum_interruption_seconds=float(
                    gpu["maximum_interruption_seconds"]
                ),
                runtime_poll_interval_seconds=float(
                    gpu["runtime_poll_interval_seconds"]
                ),
                calibration_inference_requests=int(
                    gpu["calibration_inference_requests"]
                ),
                acceptance_inference_requests=int(
                    gpu["acceptance_inference_requests"]
                ),
                sample_image_uri=str(gpu["sample_image_uri"]),
                candidate_readiness_path=data_root
                / str(gpu["candidate_readiness_relative_path"]),
                candidate_summary_path=data_root
                / str(gpu["candidate_summary_relative_path"]),
                candidate_release_submission_path=data_root
                / str(gpu["candidate_release_submission_relative_path"]),
                candidate_model_path=data_root
                / str(gpu["candidate_model_relative_path"]),
            ),
            guardrails=GuardrailConfig(
                accepted_loss=int(guardrails["accepted_loss"]),
                duplicate_effects=int(guardrails["duplicate_effects"]),
                maximum_error_rate=float(guardrails["maximum_error_rate"]),
                maximum_p99_ms=float(guardrails["maximum_p99_ms"]),
                require_exact_rollback_identity=bool(
                    guardrails["require_exact_rollback_identity"]
                ),
                require_zero_gpu_owner_overlap=bool(
                    guardrails["require_zero_gpu_owner_overlap"]
                ),
            ),
            claim_boundary=(
                "Controlled API rolling continuity and single-GPU handoff "
                f"measurements on {int(claim['physical_nodes'])} local physical "
                "node. This is not customer traffic, a production SLA, physical-"
                "node or multi-zone HA/DR, zero-downtime GPU HA, or multi-GPU evidence."
            ),
        )
        _validate_config(config, claim)
        return config

    def public_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "evm.s6_rolling_handoff_runtime.v1",
            "seed": self.seed,
            "config_sha256": self.sha256,
            "api": vars(self.api),
            "rolling": vars(self.rolling),
            "gpu_handoff": {
                key: value
                for key, value in vars(self.gpu_handoff).items()
                if not key.endswith("_path")
            },
            "guardrails": vars(self.guardrails),
            "claim_boundary": self.claim_boundary,
        }


def analyze_s6_results(
    *,
    api_repetitions: Sequence[Mapping[str, Any]],
    gpu_calibration: Mapping[str, Any],
    gpu_repetitions: Sequence[Mapping[str, Any]],
    config: S6RuntimeConfig,
) -> dict[str, Any]:
    _validate_finite(api_repetitions, "api_repetitions")
    _validate_finite(gpu_calibration, "gpu_calibration")
    _validate_finite(gpu_repetitions, "gpu_repetitions")
    api_count_ok = len(api_repetitions) == config.api.repetitions
    gpu_count_ok = len(gpu_repetitions) == config.gpu_handoff.repetitions
    expected_api_requests = round(
        config.api.target_requests_per_second * config.api.measurement_seconds
    )
    api_identity_closed = api_count_ok and all(
        int(item.get("logical_requests", -1)) == expected_api_requests
        and int(item.get("database_accepted", -1))
        == int(item.get("database_terminal", -2))
        == int(item.get("client_success", -3))
        and int(item.get("accepted_loss", -1)) == config.guardrails.accepted_loss
        and int(item.get("duplicate_effects", -1))
        == config.guardrails.duplicate_effects
        and float(item.get("error_rate", math.inf))
        <= config.guardrails.maximum_error_rate
        and int(item.get("trace_identity_matches", -1))
        == int(item.get("client_success", -2))
        and item.get("trace_complete") is True
        and int(item.get("trace_expected", -1))
        == int(item.get("trace_observed", -2))
        for item in api_repetitions
    )
    api_drain_measured = api_count_ok and all(
        int(item.get("drain_event_count", 0)) == config.api.replicas
        and 0 < float(item.get("rollout_seconds", 0))
        <= config.rolling.rollout_timeout_seconds
        and 0 <= float(item.get("maximum_drain_seconds", -1))
        <= config.rolling.drain_timeout_seconds
        and item.get("prometheus_up") is True
        and item.get("cleanup_passed") is True
        for item in api_repetitions
    )
    calibration_passed = (
        gpu_calibration.get("status") == "passed"
        and gpu_calibration.get("candidate_gate_passed") is True
        and gpu_calibration.get("approval_consumed_once") is True
        and gpu_calibration.get("zero_owner_overlap") is True
        and gpu_calibration.get("target_identity_exact") is True
        and gpu_calibration.get("rollback_exact") is True
        and gpu_calibration.get("target_cuda_inference") is True
        and gpu_calibration.get("source_cuda_inference_restored") is True
        and gpu_calibration.get("prometheus_restored") is True
        and int(gpu_calibration.get("target_inference_count", -1))
        == config.gpu_handoff.calibration_inference_requests
        and float(gpu_calibration.get("target_p99_ms", math.inf))
        <= config.guardrails.maximum_p99_ms
    )
    gpu_handoff_measured = gpu_count_ok and calibration_passed and all(
        item.get("status") == "passed"
        and item.get("approval_consumed_once") is True
        and item.get("zero_owner_overlap") is True
        and item.get("target_identity_exact") is True
        and item.get("rollback_exact") is True
        and item.get("target_cuda_inference") is True
        and item.get("source_cuda_inference_restored") is True
        and item.get("prometheus_restored") is True
        and int(item.get("target_inference_count", -1))
        == config.gpu_handoff.acceptance_inference_requests
        and 0 < float(item.get("source_to_target_interruption_seconds", 0))
        <= config.gpu_handoff.maximum_interruption_seconds
        and 0 < float(item.get("target_to_source_interruption_seconds", 0))
        <= config.gpu_handoff.maximum_interruption_seconds
        and float(item.get("target_p99_ms", math.inf))
        <= config.guardrails.maximum_p99_ms
        for item in gpu_repetitions
    )
    claim_is_bounded = (
        "zero-downtime GPU HA" in config.claim_boundary
        and "not customer traffic" in config.claim_boundary
    )
    acceptance = {
        "S6-AC-01": {
            "status": "passed" if api_identity_closed else "failed",
            "api_repetitions": len(api_repetitions),
        },
        "S6-AC-02": {
            "status": "passed" if api_drain_measured else "failed",
            "replacement_seconds": [
                float(item.get("rollout_seconds", 0)) for item in api_repetitions
            ],
            "drain_seconds": [
                float(item.get("maximum_drain_seconds", 0))
                for item in api_repetitions
            ],
        },
        "S6-AC-03": {
            "status": "passed" if gpu_handoff_measured else "failed",
            "gpu_repetitions": len(gpu_repetitions),
            "calibration_passed": calibration_passed,
        },
        "S6-AC-04": {
            "status": "passed" if claim_is_bounded else "failed",
            "high_availability_claimed": False,
        },
    }
    status = (
        "passed"
        if all(item["status"] == "passed" for item in acceptance.values())
        else "failed"
    )
    return {
        "status": status,
        "acceptance": acceptance,
        "api": {
            "repetition_count": len(api_repetitions),
            "expected_logical_requests_per_repetition": expected_api_requests,
            "accepted_total": sum(
                int(item.get("database_accepted", 0)) for item in api_repetitions
            ),
            "attempt_total": sum(int(item.get("attempts", 0)) for item in api_repetitions),
            "p99_ms": [float(item.get("p99_ms", 0)) for item in api_repetitions],
            "rollout_seconds": [
                float(item.get("rollout_seconds", 0)) for item in api_repetitions
            ],
        },
        "gpu_handoff": {
            "calibration_target_p99_ms": float(
                gpu_calibration.get("target_p99_ms", 0)
            ),
            "repetition_count": len(gpu_repetitions),
            "source_to_target_interruption_seconds": [
                float(item.get("source_to_target_interruption_seconds", 0))
                for item in gpu_repetitions
            ],
            "target_to_source_interruption_seconds": [
                float(item.get("target_to_source_interruption_seconds", 0))
                for item in gpu_repetitions
            ],
        },
        "claim_boundary": config.claim_boundary,
    }


def percentile(values: Sequence[float], quantile: float) -> float:
    if not values:
        raise S6RuntimeError("s6_percentile_empty")
    ordered = sorted(float(value) for value in values)
    if not 0 <= quantile <= 1:
        raise S6RuntimeError("s6_percentile_quantile_invalid")
    position = (len(ordered) - 1) * quantile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def summarize_latencies(values: Sequence[float]) -> dict[str, float]:
    if not values:
        raise S6RuntimeError("s6_latency_samples_empty")
    return {
        "mean_ms": statistics.fmean(values),
        "p50_ms": percentile(values, 0.50),
        "p95_ms": percentile(values, 0.95),
        "p99_ms": percentile(values, 0.99),
        "maximum_ms": max(values),
    }


def deterministic_traceparent(identity: str, *, sampled: bool) -> tuple[str, str]:
    trace_id = hashlib.sha256(f"s6-trace:{identity}".encode("utf-8")).hexdigest()[:32]
    span_id = hashlib.sha256(f"s6-span:{identity}".encode("utf-8")).hexdigest()[:16]
    return f"00-{trace_id}-{span_id}-{'01' if sampled else '00'}", trace_id


def payload_sha256(payload: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            payload,
            allow_nan=False,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _section(payload: Mapping[str, Any], name: str) -> dict[str, Any]:
    value = payload.get(name)
    if not isinstance(value, dict):
        raise S6RuntimeError(f"s6_runtime_config_section_invalid:{name}")
    return value


def _validate_config(config: S6RuntimeConfig, claim: Mapping[str, Any]) -> None:
    if config.seed < 1:
        raise S6RuntimeError("s6_seed_invalid")
    if config.api.replicas != 2 or config.api.repetitions != 3:
        raise S6RuntimeError("s6_api_replica_or_repetition_contract_invalid")
    if config.api.target_requests_per_second <= 0 or config.api.measurement_seconds <= 0:
        raise S6RuntimeError("s6_api_load_contract_invalid")
    if not 0 < config.api.rollout_offset_seconds < config.api.measurement_seconds:
        raise S6RuntimeError("s6_rollout_offset_invalid")
    if config.rolling.max_unavailable != 0 or config.rolling.max_surge != 1:
        raise S6RuntimeError("s6_zero_unavailable_contract_invalid")
    if config.gpu_handoff.repetitions != 3:
        raise S6RuntimeError("s6_gpu_repetition_contract_invalid")
    if config.gpu_handoff.calibration_inference_requests < 20:
        raise S6RuntimeError("s6_gpu_calibration_sample_invalid")
    if config.gpu_handoff.acceptance_inference_requests < 20:
        raise S6RuntimeError("s6_gpu_acceptance_sample_invalid")
    if int(claim.get("physical_nodes", 0)) != 1 or int(claim.get("gpu_count", 0)) != 1:
        raise S6RuntimeError("s6_claim_resource_boundary_invalid")
    if any(
        bool(claim.get(key, True))
        for key in ("customer_traffic", "production_sla", "high_availability", "multi_gpu")
    ):
        raise S6RuntimeError("s6_claim_boundary_invalid")
    for path in (
        config.gpu_handoff.candidate_readiness_path,
        config.gpu_handoff.candidate_summary_path,
        config.gpu_handoff.candidate_release_submission_path,
        config.gpu_handoff.candidate_model_path,
    ):
        if not path.is_file():
            raise S6RuntimeError(f"s6_candidate_evidence_missing:{path.name}")


def _validate_finite(value: Any, path: str) -> None:
    if isinstance(value, float) and not math.isfinite(value):
        raise S6RuntimeError(f"s6_non_finite:{path}")
    if isinstance(value, Mapping):
        for key, item in value.items():
            _validate_finite(item, f"{path}.{key}")
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for index, item in enumerate(value):
            _validate_finite(item, f"{path}[{index}]")
