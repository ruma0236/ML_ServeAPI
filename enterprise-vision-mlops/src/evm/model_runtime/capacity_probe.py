from __future__ import annotations

import hashlib
import json
import math
import os
import time
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from prometheus_client import Counter, Gauge, Histogram

from evm.control_panel.scenario_workloads import (
    CapacityProbeCatalog,
    CapacityProbeDescriptor,
    CapacityProbeFamily,
    CapacityProbeRequest,
    CapacityProbeResponse,
    CapacityProbeStageTimings,
)
from evm.observability.otel import trace_span


EXPECTED_FAMILIES: tuple[CapacityProbeFamily, ...] = (
    "logistic",
    "probabilistic",
    "online-linear",
    "branch-heavy",
    "incremental",
)
EXPECTED_MODEL_TYPES: dict[CapacityProbeFamily, str] = {
    "logistic": "linear_logit",
    "probabilistic": "gaussian_nb",
    "online-linear": "linear_logit",
    "branch-heavy": "decision_tree",
    "incremental": "linear_logit",
}
DEFAULT_REGISTRY_PATH = (
    "F:/EnterpriseMLOps_Data/enterprise-vision-mlops/"
    "artifacts/scale_validation/s3/capacity-registry.json"
)

CAPACITY_PROBE_REQUESTS = Counter(
    "evm_s3_capacity_probe_requests_total",
    "S3 tabular capacity requests by bounded probe family and outcome.",
    ("probe_family", "outcome"),
)
CAPACITY_PROBE_STAGE_SECONDS = Histogram(
    "evm_s3_capacity_probe_stage_seconds",
    "S3 validation, transform, and prediction stage latency.",
    ("probe_family", "stage"),
    buckets=(0.00001, 0.000025, 0.00005, 0.0001, 0.00025, 0.0005, 0.001, 0.0025, 0.005, 0.01, 0.025, 0.05, 0.1),
)
CAPACITY_PROBE_IN_FLIGHT = Gauge(
    "evm_s3_capacity_probe_in_flight",
    "Current S3 tabular capacity requests by bounded probe family.",
    ("probe_family",),
)
CAPACITY_PROBE_MODEL_INFO = Gauge(
    "evm_s3_capacity_probe_model_info",
    "Loaded S3 tabular probe identity with bounded labels.",
    ("probe_family", "algorithm", "dataset_version"),
)


class CapacityProbeError(RuntimeError):
    def __init__(self, code: str, message: str, *, status_code: int = 422):
        super().__init__(message)
        self.code = code
        self.status_code = status_code


@dataclass(frozen=True)
class LoadedCapacityProbe:
    family: CapacityProbeFamily
    algorithm: str
    dataset_identity_sha256: str
    dataset_version: str
    artifact_sha256: str
    model_identity_sha256: str
    payload: dict[str, Any]


def capacity_registry_path() -> Path:
    return Path(os.getenv("EVM_S3_CAPACITY_REGISTRY_PATH", DEFAULT_REGISTRY_PATH))


def clear_capacity_probe_cache() -> None:
    _read_json_cached.cache_clear()


def load_capacity_probe_catalog() -> CapacityProbeCatalog:
    registry_path = capacity_registry_path()
    registry = _load_registry(registry_path)
    descriptors = [
        CapacityProbeDescriptor(
            probe_family=family,
            algorithm=probe.algorithm,
            model_identity_sha256=probe.model_identity_sha256,
            artifact_sha256=probe.artifact_sha256,
        )
        for family in EXPECTED_FAMILIES
        for probe in [_load_probe(registry_path, registry, family)]
    ]
    return CapacityProbeCatalog(
        dataset_version=str(registry["dataset_version"]),
        dataset_identity_sha256=str(registry["dataset_identity_sha256"]),
        split_manifest_sha256=str(registry["split_manifest_sha256"]),
        source_uri=str(registry["source_uri"]),
        probes=descriptors,
    )


def run_capacity_probe(request: CapacityProbeRequest) -> CapacityProbeResponse:
    family = request.probe_family
    CAPACITY_PROBE_IN_FLIGHT.labels(probe_family=family).inc()
    started = time.perf_counter()
    outcome = "error"
    try:
        validation_started = time.perf_counter()
        with trace_span(
            "s3.capacity.validation",
            attributes={"evm.stage": "validation", "evm.probe_family": family},
        ):
            registry_path = capacity_registry_path()
            registry = _load_registry(registry_path)
            if request.dataset_identity_sha256 != registry["dataset_identity_sha256"]:
                raise CapacityProbeError(
                    "capacity_dataset_identity_mismatch",
                    "Request dataset identity does not match the loaded S3 registry.",
                    status_code=409,
                )
            if any(not math.isfinite(value) for value in request.features):
                raise CapacityProbeError(
                    "capacity_features_non_finite",
                    "Capacity probe features must be finite.",
                )
            probe = _load_probe(registry_path, registry, family)
        validation_seconds = time.perf_counter() - validation_started
        _observe_stage(family, "validation", validation_seconds)

        transform_started = time.perf_counter()
        with trace_span(
            "s3.capacity.transform",
            attributes={"evm.stage": "transform", "evm.probe_family": family},
        ):
            transformed = _transform(request.features, probe.payload)
        transform_seconds = time.perf_counter() - transform_started
        _observe_stage(family, "transform", transform_seconds)

        prediction_started = time.perf_counter()
        with trace_span(
            "s3.capacity.prediction",
            attributes={"evm.stage": "prediction", "evm.probe_family": family},
        ):
            positive_probability = _predict_probability(transformed, probe.payload)
        prediction_seconds = time.perf_counter() - prediction_started
        _observe_stage(family, "prediction", prediction_seconds)

        total_seconds = time.perf_counter() - started
        CAPACITY_PROBE_MODEL_INFO.labels(
            probe_family=family,
            algorithm=probe.algorithm,
            dataset_version=probe.dataset_version,
        ).set(1)
        outcome = "ok"
        return CapacityProbeResponse(
            probe_family=family,
            dataset_identity_sha256=probe.dataset_identity_sha256,
            model_identity_sha256=probe.model_identity_sha256,
            prediction=1 if positive_probability >= 0.5 else 0,
            positive_probability=positive_probability,
            timings=CapacityProbeStageTimings(
                validation_ms=validation_seconds * 1000,
                transform_ms=transform_seconds * 1000,
                prediction_ms=prediction_seconds * 1000,
                total_ms=total_seconds * 1000,
            ),
        )
    except CapacityProbeError:
        outcome = "rejected"
        raise
    finally:
        CAPACITY_PROBE_REQUESTS.labels(probe_family=family, outcome=outcome).inc()
        CAPACITY_PROBE_IN_FLIGHT.labels(probe_family=family).dec()


def _observe_stage(family: CapacityProbeFamily, stage: str, elapsed_seconds: float) -> None:
    CAPACITY_PROBE_STAGE_SECONDS.labels(probe_family=family, stage=stage).observe(
        elapsed_seconds
    )


def _load_registry(path: Path) -> dict[str, Any]:
    payload = _read_json(path, "capacity_registry")
    if payload.get("schema_version") != "evm.s3_capacity_registry.v1":
        raise CapacityProbeError(
            "capacity_registry_schema_invalid",
            "S3 capacity registry schema is invalid.",
            status_code=503,
        )
    required = {
        "dataset_version",
        "dataset_identity_sha256",
        "split_manifest_sha256",
        "source_uri",
        "source_doi",
        "license",
        "feature_count",
        "probes",
    }
    if required - payload.keys():
        raise CapacityProbeError(
            "capacity_registry_incomplete",
            "S3 capacity registry is missing required fields.",
            status_code=503,
        )
    if (
        payload.get("dataset_id") != "uci-higgs"
        or payload.get("source_doi") != "10.24432/C5V312"
        or payload.get("license") != "CC BY 4.0"
        or payload.get("feature_count") != 28
        or not _is_sha256(payload.get("dataset_identity_sha256"))
        or not _is_sha256(payload.get("split_manifest_sha256"))
    ):
        raise CapacityProbeError(
            "capacity_registry_identity_invalid",
            "S3 dataset identity contract is invalid.",
            status_code=503,
        )
    probes = payload.get("probes")
    if not isinstance(probes, dict) or set(probes) != set(EXPECTED_FAMILIES):
        raise CapacityProbeError(
            "capacity_registry_probe_set_invalid",
            "S3 capacity registry must contain exactly the five frozen probe families.",
            status_code=503,
        )
    return payload


def _load_probe(
    registry_path: Path,
    registry: dict[str, Any],
    family: CapacityProbeFamily,
) -> LoadedCapacityProbe:
    entry = registry["probes"].get(family)
    if not isinstance(entry, dict):
        raise CapacityProbeError(
            "capacity_probe_missing",
            f"Capacity probe {family} is not registered.",
            status_code=503,
        )
    artifact_uri = entry.get("artifact_uri")
    if not isinstance(artifact_uri, str):
        raise CapacityProbeError(
            "capacity_probe_artifact_missing",
            f"Capacity probe {family} has no artifact URI.",
            status_code=503,
        )
    artifact_path = _resolve_registry_artifact(registry_path, artifact_uri)
    artifact_bytes = _read_bytes(artifact_path, "capacity_probe_artifact")
    artifact_sha256 = hashlib.sha256(artifact_bytes).hexdigest()
    if artifact_sha256 != entry.get("artifact_sha256"):
        raise CapacityProbeError(
            "capacity_probe_artifact_digest_mismatch",
            f"Capacity probe {family} artifact digest does not match.",
            status_code=503,
        )
    payload = _read_json_bytes(artifact_bytes, "capacity_probe_artifact")
    algorithm = str(entry.get("algorithm") or "")
    if (
        payload.get("schema_version") != "evm.s3_capacity_probe_artifact.v1"
        or payload.get("probe_family") != family
        or payload.get("model_type") != EXPECTED_MODEL_TYPES[family]
        or payload.get("model_type") != algorithm
        or payload.get("feature_count") != 28
        or payload.get("dataset_identity_sha256") != registry["dataset_identity_sha256"]
    ):
        raise CapacityProbeError(
            "capacity_probe_artifact_contract_invalid",
            f"Capacity probe {family} artifact contract is invalid.",
            status_code=503,
        )
    model_identity = _model_identity(
        family=family,
        dataset_identity_sha256=str(registry["dataset_identity_sha256"]),
        artifact_sha256=artifact_sha256,
        algorithm=algorithm,
    )
    if model_identity != entry.get("model_identity_sha256"):
        raise CapacityProbeError(
            "capacity_probe_model_identity_mismatch",
            f"Capacity probe {family} model identity does not match.",
            status_code=503,
        )
    return LoadedCapacityProbe(
        family=family,
        algorithm=algorithm,
        dataset_identity_sha256=str(registry["dataset_identity_sha256"]),
        dataset_version=str(registry["dataset_version"]),
        artifact_sha256=artifact_sha256,
        model_identity_sha256=model_identity,
        payload=payload,
    )


def _transform(features: list[float], payload: dict[str, Any]) -> list[float]:
    transform = payload.get("transform")
    if not isinstance(transform, dict):
        raise CapacityProbeError("capacity_transform_missing", "Probe transform is missing.", status_code=503)
    kind = transform.get("kind")
    if kind == "identity":
        return list(features)
    if kind != "standardize":
        raise CapacityProbeError("capacity_transform_invalid", "Probe transform is invalid.", status_code=503)
    mean = _float_vector(transform.get("mean"), "transform.mean")
    scale = _float_vector(transform.get("scale"), "transform.scale")
    if any(value <= 0 for value in scale):
        raise CapacityProbeError("capacity_transform_scale_invalid", "Probe scale must be positive.", status_code=503)
    return [(value - mean[index]) / scale[index] for index, value in enumerate(features)]


def _predict_probability(features: list[float], payload: dict[str, Any]) -> float:
    model_type = payload["model_type"]
    model = payload.get("model")
    if not isinstance(model, dict):
        raise CapacityProbeError("capacity_model_missing", "Probe model payload is missing.", status_code=503)
    if model_type == "linear_logit":
        weights = _float_vector(model.get("weights"), "model.weights")
        intercept = _finite_float(model.get("intercept"), "model.intercept")
        score = math.fsum(weight * value for weight, value in zip(weights, features, strict=True))
        return _sigmoid(score + intercept)
    if model_type == "gaussian_nb":
        theta = _two_class_matrix(model.get("theta"), "model.theta")
        variance = _two_class_matrix(model.get("variance"), "model.variance")
        priors = _float_vector(model.get("class_log_prior"), "model.class_log_prior", size=2)
        scores = []
        for class_index in range(2):
            if any(value <= 0 for value in variance[class_index]):
                raise CapacityProbeError(
                    "capacity_model_variance_invalid",
                    "Gaussian variance must be positive.",
                    status_code=503,
                )
            score = priors[class_index] + math.fsum(
                -0.5
                * (
                    math.log(2 * math.pi * variance[class_index][feature_index])
                    + (value - theta[class_index][feature_index]) ** 2
                    / variance[class_index][feature_index]
                )
                for feature_index, value in enumerate(features)
            )
            scores.append(score)
        return _sigmoid(scores[1] - scores[0])
    if model_type == "decision_tree":
        return _tree_probability(features, model)
    raise CapacityProbeError("capacity_model_type_invalid", "Probe model type is invalid.", status_code=503)


def _tree_probability(features: list[float], model: dict[str, Any]) -> float:
    nodes = model.get("nodes")
    if not isinstance(nodes, list) or not nodes:
        raise CapacityProbeError("capacity_tree_invalid", "Decision tree nodes are invalid.", status_code=503)
    index = 0
    for _ in range(len(nodes) + 1):
        if not 0 <= index < len(nodes) or not isinstance(nodes[index], dict):
            break
        node = nodes[index]
        if "positive_probability" in node:
            probability = _finite_float(
                node.get("positive_probability"),
                "model.nodes.positive_probability",
            )
            if not 0 <= probability <= 1:
                break
            return probability
        feature = node.get("feature")
        threshold = node.get("threshold")
        left = node.get("left")
        right = node.get("right")
        if (
            not isinstance(feature, int)
            or not 0 <= feature < 28
            or not isinstance(left, int)
            or not isinstance(right, int)
        ):
            break
        index = left if features[feature] <= _finite_float(threshold, "model.nodes.threshold") else right
    raise CapacityProbeError("capacity_tree_invalid", "Decision tree traversal is invalid.", status_code=503)


def _float_vector(value: Any, field: str, *, size: int = 28) -> list[float]:
    if not isinstance(value, list) or len(value) != size:
        raise CapacityProbeError("capacity_model_shape_invalid", f"{field} must have {size} values.", status_code=503)
    return [_finite_float(item, field) for item in value]


def _two_class_matrix(value: Any, field: str) -> list[list[float]]:
    if not isinstance(value, list) or len(value) != 2:
        raise CapacityProbeError("capacity_model_shape_invalid", f"{field} must have two rows.", status_code=503)
    return [_float_vector(row, field) for row in value]


def _finite_float(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value):
        raise CapacityProbeError("capacity_model_value_invalid", f"{field} must be finite.", status_code=503)
    return float(value)


def _sigmoid(value: float) -> float:
    if value >= 0:
        return 1.0 / (1.0 + math.exp(-value))
    exp_value = math.exp(value)
    return exp_value / (1.0 + exp_value)


def _model_identity(
    *,
    family: CapacityProbeFamily,
    dataset_identity_sha256: str,
    artifact_sha256: str,
    algorithm: str,
) -> str:
    material = json.dumps(
        {
            "schema_version": "evm.s3_capacity_model_identity.v1",
            "probe_family": family,
            "dataset_identity_sha256": dataset_identity_sha256,
            "artifact_sha256": artifact_sha256,
            "algorithm": algorithm,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(material).hexdigest()


def _resolve_registry_artifact(registry_path: Path, uri: str) -> Path:
    candidate = Path(uri)
    if candidate.is_absolute() or candidate.drive or ".." in candidate.parts:
        raise CapacityProbeError(
            "capacity_probe_artifact_path_invalid",
            "Capacity probe artifact URI must be repository-root-relative to its registry.",
            status_code=503,
        )
    root = registry_path.resolve().parent
    resolved = (root / candidate).resolve()
    if not resolved.is_relative_to(root):
        raise CapacityProbeError(
            "capacity_probe_artifact_path_invalid",
            "Capacity probe artifact escapes its registry root.",
            status_code=503,
        )
    return resolved


def _read_json(path: Path, label: str) -> dict[str, Any]:
    try:
        stat = path.stat()
    except OSError as exc:
        raise CapacityProbeError(f"{label}_unavailable", str(exc), status_code=503) from exc
    return _read_json_cached(str(path.resolve()), stat.st_mtime_ns, stat.st_size, label)


@lru_cache(maxsize=32)
def _read_json_cached(path: str, mtime_ns: int, size: int, label: str) -> dict[str, Any]:
    del mtime_ns, size
    return _read_json_bytes(_read_bytes(Path(path), label), label)


def _read_bytes(path: Path, label: str) -> bytes:
    try:
        return path.read_bytes()
    except OSError as exc:
        raise CapacityProbeError(f"{label}_unavailable", str(exc), status_code=503) from exc


def _read_json_bytes(payload: bytes, label: str) -> dict[str, Any]:
    try:
        value = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise CapacityProbeError(f"{label}_invalid", str(exc), status_code=503) from exc
    if not isinstance(value, dict):
        raise CapacityProbeError(f"{label}_invalid", f"{label} must be an object.", status_code=503)
    return value


def _is_sha256(value: Any) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(
        character in "0123456789abcdef" for character in value
    )
