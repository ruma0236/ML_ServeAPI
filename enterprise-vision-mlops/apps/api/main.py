import json
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests
from fastapi import FastAPI, HTTPException, Response
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, Histogram, generate_latest
from pydantic import BaseModel, Field

from apps.api.control_panel_commands import router as control_panel_commands_router
from apps.api.control_panel_deployments import router as control_panel_deployments_router
from apps.api.control_panel_governance import router as control_panel_governance_router
from apps.api.control_panel_orchestrators import router as control_panel_orchestrators_router
from apps.api.control_panel_profiles import router as control_panel_profiles_router
from apps.api.control_panel_tasks import router as control_panel_tasks_router
from apps.api.control_panel import router as control_panel_router
from evm.core.image_feature_model import extract_image_features, predict_with_model, resolve_image_path


APP_NAME = os.getenv("APP_NAME", "enterprise-vision-mlops-api")
MLFLOW_TRACKING_URI = os.getenv("MLFLOW_TRACKING_URI", "http://localhost:5000")
MODEL_NAME = os.getenv("MODEL_NAME", "vision-baseline")
MODEL_STAGE = os.getenv("MODEL_STAGE", "Production")
MODEL_REGISTRY_PATH = Path(
    os.getenv(
        "MODEL_REGISTRY_PATH",
        f"artifacts/registry/{MODEL_NAME}/latest.json",
    )
)
EVM_HOST_DATA_ROOT = os.getenv("EVM_HOST_DATA_ROOT", "F:/EnterpriseMLOps_Data/enterprise-vision-mlops")
EVM_DATA_MOUNT_ROOT = os.getenv("EVM_DATA_MOUNT_ROOT", "/mnt/evm-data")
VLM_OBSERVABILITY_PATH = Path(
    os.getenv(
        "VLM_OBSERVABILITY_PATH",
        "artifacts/vlm/observability/benchmark_report.json",
    )
)

REQUEST_COUNT = Counter(
    "evm_inference_requests_total",
    "Total number of inference requests.",
    ["endpoint", "status"],
)
REQUEST_LATENCY = Histogram(
    "evm_inference_latency_seconds",
    "Inference latency in seconds.",
    ["endpoint"],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5),
)
MODEL_LOADED = Gauge(
    "evm_serving_model_loaded",
    "Whether a promoted registry model is loaded by the serving API.",
    ["model_name", "model_stage"],
)
MODEL_VERSION = Gauge(
    "evm_serving_model_version",
    "Loaded serving model registry version.",
    ["model_name", "model_stage"],
)
MODEL_INFO = Gauge(
    "evm_serving_model_info",
    "Loaded serving model metadata.",
    ["model_name", "model_stage", "model_version", "dataset_version"],
)
_MODEL_METRIC_LOCK = threading.Lock()
_MODEL_LOADED_LABELS: set[tuple[str, str]] = set()
_MODEL_VERSION_LABELS: set[tuple[str, str]] = set()
_MODEL_INFO_LABELS: set[tuple[str, str, str, str]] = set()
VLM_SCHEMA_VALID_RATE = Gauge(
    "evm_vlm_schema_valid_rate",
    "Latest VLM batch schema validity rate from observability artifacts.",
)
VLM_P95_LATENCY_MS = Gauge(
    "evm_vlm_p95_latency_ms",
    "Latest VLM batch p95 latency in milliseconds from observability artifacts.",
)
VLM_QUALITY_ERROR_COUNT = Gauge(
    "evm_vlm_quality_error_count",
    "Latest image quality fatal error count from observability artifacts.",
)
VLM_AUDIT_EVENT_COUNT = Gauge(
    "evm_vlm_audit_event_count",
    "Latest VLM audit event count from observability artifacts.",
)


@dataclass(frozen=True)
class LoadedModel:
    registry_path: str
    model_name: str
    model_stage: str
    model_version: str
    model_type: str
    prediction: str
    confidence: float
    dataset_version: str
    validated_parquet_uri: str
    source_model: dict[str, Any]

    def ready_payload(self) -> dict[str, Any]:
        return {
            "model_loaded": True,
            "model_name": self.model_name,
            "model_stage": self.model_stage,
            "model_version": self.model_version,
            "model_type": self.model_type,
            "dataset_version": self.dataset_version,
            "validated_parquet_uri": self.validated_parquet_uri,
            "registry_path": self.registry_path,
        }


class PredictRequest(BaseModel):
    image_uri: str | None = Field(
        default=None,
        description="Object storage URI or local path for an image payload.",
    )
    features: dict[str, Any] = Field(
        default_factory=dict,
        description="Optional structured metadata for smoke tests.",
    )


class PredictResponse(BaseModel):
    model_name: str
    model_stage: str
    model_version: str
    dataset_version: str
    validated_parquet_uri: str
    prediction: str
    confidence: float
    scores: dict[str, float] = Field(default_factory=dict)
    latency_ms: float
    placeholder: bool
    feature_source: str
    registry_path: str


app = FastAPI(title=APP_NAME, version="0.1.0")
app.include_router(control_panel_router)
app.include_router(control_panel_tasks_router)
app.include_router(control_panel_commands_router)
app.include_router(control_panel_deployments_router)
app.include_router(control_panel_governance_router)
app.include_router(control_panel_orchestrators_router)
app.include_router(control_panel_profiles_router)
MODEL_STATE: LoadedModel | None = None
MODEL_LOAD_ERROR = ""


def _coerce_confidence(metrics: dict[str, Any]) -> float:
    for key in ("baseline_accuracy", "accuracy", "confidence"):
        value = metrics.get(key)
        if isinstance(value, int | float):
            return max(0.0, min(1.0, float(value)))
    return 0.0


def _load_registry_model(path: Path) -> LoadedModel:
    payload = json.loads(path.read_text(encoding="utf-8"))
    source_model = payload.get("source_model")
    if not isinstance(source_model, dict):
        raise ValueError("registry payload is missing source_model")

    dataset = source_model.get("dataset", {})
    if not isinstance(dataset, dict):
        dataset = {}
    metrics = source_model.get("metrics", {})
    if not isinstance(metrics, dict):
        metrics = {}

    model_name = str(payload.get("model_name") or source_model.get("model_name") or MODEL_NAME)
    model_stage = str(payload.get("stage") or MODEL_STAGE)
    model_version = str(payload.get("version") or "")
    if not model_version:
        raise ValueError("registry payload is missing version")

    prediction = str(source_model.get("prediction") or "unknown")
    return LoadedModel(
        registry_path=str(path),
        model_name=model_name,
        model_stage=model_stage,
        model_version=model_version,
        model_type=str(source_model.get("model_type") or "unknown"),
        prediction=prediction,
        confidence=_coerce_confidence(metrics),
        dataset_version=str(dataset.get("dataset_version") or ""),
        validated_parquet_uri=str(dataset.get("validated_parquet_uri") or ""),
        source_model=source_model,
    )


def _request_features(payload: PredictRequest) -> tuple[dict[str, Any], str]:
    if payload.features:
        return dict(payload.features), "request_features"

    image_path = resolve_image_path(
        payload.image_uri,
        host_data_root=EVM_HOST_DATA_ROOT,
        data_mount_root=EVM_DATA_MOUNT_ROOT,
    )
    if image_path is None or not image_path.exists():
        raise HTTPException(
            status_code=422,
            detail={
                "error": "image_uri is not readable and no features were supplied",
                "image_uri": payload.image_uri,
                "resolved_path": str(image_path) if image_path else "",
                "host_data_root": EVM_HOST_DATA_ROOT,
                "data_mount_root": EVM_DATA_MOUNT_ROOT,
            },
        )
    return extract_image_features(image_path), "image_uri"


def _predict(model: LoadedModel, payload: PredictRequest) -> tuple[str, float, dict[str, float], str]:
    if model.model_type == "image_feature_centroid":
        features, feature_source = _request_features(payload)
        result = predict_with_model(model.source_model, features)
        scores = {
            str(label): round(float(score), 6)
            for label, score in result.get("scores", {}).items()
        }
        return (
            str(result.get("prediction", "unknown")),
            round(float(result.get("confidence", 0.0)), 6),
            scores,
            feature_source,
        )
    return model.prediction, model.confidence, {}, "registry_default"


def _remove_metric_child(metric: Gauge, labels: tuple[str, ...]) -> None:
    try:
        metric.remove(*labels)
    except KeyError:
        pass


def _clear_model_metrics() -> None:
    for labels in list(_MODEL_LOADED_LABELS):
        _remove_metric_child(MODEL_LOADED, labels)
    for labels in list(_MODEL_VERSION_LABELS):
        _remove_metric_child(MODEL_VERSION, labels)
    for labels in list(_MODEL_INFO_LABELS):
        _remove_metric_child(MODEL_INFO, labels)
    _MODEL_LOADED_LABELS.clear()
    _MODEL_VERSION_LABELS.clear()
    _MODEL_INFO_LABELS.clear()


def refresh_model_state() -> LoadedModel | None:
    global MODEL_LOAD_ERROR, MODEL_STATE

    try:
        model = _load_registry_model(MODEL_REGISTRY_PATH)
    except Exception as exc:
        MODEL_STATE = None
        MODEL_LOAD_ERROR = str(exc)
        with _MODEL_METRIC_LOCK:
            _clear_model_metrics()
            labels = (MODEL_NAME, MODEL_STAGE)
            MODEL_LOADED.labels(model_name=MODEL_NAME, model_stage=MODEL_STAGE).set(0)
            _MODEL_LOADED_LABELS.add(labels)
        return None

    MODEL_STATE = model
    MODEL_LOAD_ERROR = ""
    with _MODEL_METRIC_LOCK:
        _clear_model_metrics()
        loaded_labels = (model.model_name, model.model_stage)
        info_labels = (
            model.model_name,
            model.model_stage,
            model.model_version,
            model.dataset_version or "unknown",
        )
        MODEL_LOADED.labels(model_name=model.model_name, model_stage=model.model_stage).set(1)
        _MODEL_LOADED_LABELS.add(loaded_labels)
        try:
            MODEL_VERSION.labels(model_name=model.model_name, model_stage=model.model_stage).set(
                float(model.model_version)
            )
        except ValueError:
            MODEL_VERSION.labels(model_name=model.model_name, model_stage=model.model_stage).set(0)
        _MODEL_VERSION_LABELS.add(loaded_labels)
        MODEL_INFO.labels(
            model_name=model.model_name,
            model_stage=model.model_stage,
            model_version=model.model_version,
            dataset_version=model.dataset_version or "unknown",
        ).set(1)
        _MODEL_INFO_LABELS.add(info_labels)
    return model


def refresh_vlm_observability_state() -> None:
    if not VLM_OBSERVABILITY_PATH.exists():
        return
    try:
        payload = json.loads(VLM_OBSERVABILITY_PATH.read_text(encoding="utf-8"))
    except Exception:
        return
    vlm_batch = payload.get("vlm_batch", {})
    dataset_quality = payload.get("dataset_quality", {})
    audit = payload.get("audit", {})
    if isinstance(vlm_batch, dict):
        VLM_SCHEMA_VALID_RATE.set(float(vlm_batch.get("schema_valid_rate", 0.0) or 0.0))
        VLM_P95_LATENCY_MS.set(float(vlm_batch.get("p95_latency_ms", 0.0) or 0.0))
    if isinstance(dataset_quality, dict):
        VLM_QUALITY_ERROR_COUNT.set(float(dataset_quality.get("error_count", 0) or 0))
    if isinstance(audit, dict):
        VLM_AUDIT_EVENT_COUNT.set(float(audit.get("event_count", 0) or 0))


@app.on_event("startup")
def startup_load_model() -> None:
    refresh_model_state()


@app.get("/health")
def health() -> dict[str, str]:
    REQUEST_COUNT.labels(endpoint="/health", status="ok").inc()
    return {"status": "ok", "service": APP_NAME}


@app.get("/ready")
def ready() -> dict[str, Any]:
    try:
        response = requests.get(f"{MLFLOW_TRACKING_URI}/health", timeout=2)
        mlflow_ready = response.ok
    except requests.RequestException:
        mlflow_ready = False

    model = refresh_model_state()
    model_loaded = model is not None
    status = "ok" if mlflow_ready and model_loaded else "degraded"
    REQUEST_COUNT.labels(endpoint="/ready", status=status).inc()
    payload: dict[str, Any] = {
        "status": status,
        "mlflow_tracking_uri": MLFLOW_TRACKING_URI,
        "mlflow_ready": mlflow_ready,
        "model_loaded": model_loaded,
        "model_load_error": MODEL_LOAD_ERROR,
    }
    if model:
        payload.update(model.ready_payload())
    else:
        payload["registry_path"] = str(MODEL_REGISTRY_PATH)
    return payload


@app.post("/predict", response_model=PredictResponse)
def predict(payload: PredictRequest) -> PredictResponse:
    start = time.perf_counter()
    model = MODEL_STATE or refresh_model_state()
    if model is None:
        REQUEST_COUNT.labels(endpoint="/predict", status="unavailable").inc()
        raise HTTPException(
            status_code=503,
            detail={
                "error": "model registry artifact is not loaded",
                "registry_path": str(MODEL_REGISTRY_PATH),
                "model_load_error": MODEL_LOAD_ERROR,
            },
        )

    prediction, confidence, scores, feature_source = _predict(model, payload)
    latency_ms = (time.perf_counter() - start) * 1000
    REQUEST_LATENCY.labels(endpoint="/predict").observe(latency_ms / 1000)
    REQUEST_COUNT.labels(endpoint="/predict", status="ok").inc()

    return PredictResponse(
        model_name=model.model_name,
        model_stage=model.model_stage,
        model_version=model.model_version,
        dataset_version=model.dataset_version,
        validated_parquet_uri=model.validated_parquet_uri,
        prediction=prediction,
        confidence=confidence,
        scores=scores,
        latency_ms=round(latency_ms, 3),
        placeholder=False,
        feature_source=feature_source,
        registry_path=model.registry_path,
    )


@app.get("/metrics")
def metrics() -> Response:
    refresh_vlm_observability_state()
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
