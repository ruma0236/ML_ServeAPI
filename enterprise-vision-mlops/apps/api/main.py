import json
import logging
import os
import threading
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, Histogram, generate_latest
from pydantic import BaseModel, Field

from apps.api.control_panel_commands import router as control_panel_commands_router
from apps.api.control_panel_ct import router as control_panel_ct_router
from apps.api.control_panel_deployments import router as control_panel_deployments_router
from apps.api.control_panel_experiments import router as control_panel_experiments_router
from apps.api.control_panel_governance import router as control_panel_governance_router
from apps.api.control_panel_incidents import (
    refresh_incident_plane_metrics,
    router as control_panel_incidents_router,
)
from apps.api.control_panel_lifecycle import router as control_panel_lifecycle_router
from apps.api.control_panel_orchestrators import router as control_panel_orchestrators_router
from apps.api.control_panel_profiles import router as control_panel_profiles_router
from apps.api.control_panel_scenarios import router as control_panel_scenarios_router
from apps.api.control_panel_tasks import router as control_panel_tasks_router
from apps.api.control_panel_workloads import router as control_panel_workloads_router
from apps.api.control_panel_runtime import router as control_panel_runtime_router
from apps.api.control_panel import router as control_panel_router
from apps.api.task_ingress import TaskIngressBodyLimitMiddleware
from evm.control_panel.operations import (
    sync_task_json_mirror_from_store,
    verify_task_json_mirror_parity,
)
from evm.core.image_feature_model import (
    extract_image_features,
    predict_with_model,
    resolve_image_path,
)
from evm.observability.trace_context import (
    TraceContextError,
    W3CTraceContext,
)
from evm.observability.otel import (
    configure_tracing,
    runtime_service_version,
    shutdown_tracing,
    trace_span,
)
from evm.control_panel.admission_queue import (
    admission_queue_mode,
    load_admission_queue_config,
)
from evm.control_panel.transactional_store import (
    ControlPlaneParityError,
    get_transactional_store,
)
from evm.operations.metrics import OperationalMetrics, load_metric_projection
from evm.model_runtime.capacity_executor import shutdown_capacity_probe_executor
from evm.model_runtime.gpu_batch_probe import shutdown_gpu_batch_probe_executor
from evm.control_panel.api_rollout import API_DRAIN_CONTROLLER, ApiDrainingError


APP_NAME = os.getenv("APP_NAME", "enterprise-vision-mlops-api")
LOGGER = logging.getLogger(__name__)
MLFLOW_TRACKING_URI = os.getenv("MLFLOW_TRACKING_URI", "http://localhost:5000")
MODEL_NAME = os.getenv("MODEL_NAME", "vision-baseline")
MODEL_STAGE = os.getenv("MODEL_STAGE", "Production")
MODEL_REGISTRY_PATH = Path(
    os.getenv(
        "MODEL_REGISTRY_PATH",
        f"artifacts/registry/{MODEL_NAME}/latest.json",
    )
)
EVM_HOST_DATA_ROOT = os.getenv(
    "EVM_HOST_DATA_ROOT", "F:/EnterpriseMLOps_Data/enterprise-vision-mlops"
)
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
HTTP_REQUEST_COUNT = Counter(
    "evm_http_server_requests_total",
    "HTTP server requests grouped by bounded route and status class.",
    ["method", "route", "status_class"],
)
HTTP_REQUEST_LATENCY = Histogram(
    "evm_http_server_duration_seconds",
    "HTTP server duration grouped by bounded route and method.",
    ["method", "route"],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10),
)
HTTP_REQUESTS_IN_FLIGHT = Gauge(
    "evm_http_server_in_flight",
    "HTTP requests currently accepted by this API process.",
)
HTTP_REQUESTS_PEAK_IN_FLIGHT = Gauge(
    "evm_http_server_peak_in_flight",
    "Highest HTTP in-flight request count observed by this API process.",
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
CONTROL_PANEL_LIFECYCLE_RUNS = Gauge(
    "evm_control_panel_lifecycle_run_count",
    "LifecycleRun count grouped by state and execution mode.",
    ["state", "execution_mode"],
)
CONTROL_PANEL_STAGES = Gauge(
    "evm_control_panel_stage_count",
    "Lifecycle stage count grouped by stage, state, and runtime.",
    ["stage", "state", "runtime"],
)
CONTROL_PANEL_STAGE_PROGRESS = Gauge(
    "evm_control_panel_stage_progress_ratio",
    "Progress ratio for recent lifecycle stages.",
    ["run_id", "stage"],
)
CONTROL_PANEL_HANDOFFS = Gauge(
    "evm_control_panel_stage_handoff_count",
    "Stage handoff count grouped by work bucket.",
    ["bucket"],
)
CONTROL_PANEL_MODEL_CANDIDATES = Gauge(
    "evm_control_panel_model_candidate_count",
    "Model candidate count grouped by architecture, status, and promotion eligibility.",
    ["architecture", "status", "selectable"],
)
CONTROL_PANEL_WORKER_ONLINE = Gauge(
    "evm_control_panel_lifecycle_worker_online",
    "Whether the host lifecycle worker heartbeat is online.",
)
CONTROL_PANEL_RUNTIME_SUPERVISOR_HEALTHY = Gauge(
    "evm_control_panel_runtime_supervisor_healthy",
    "Whether the local host runtime supervisor and enabled children are healthy.",
)
CONTROL_PANEL_RUNTIME_CHILD_STATE = Gauge(
    "evm_control_panel_runtime_child_state",
    "Current lifecycle host child state as a one-hot gauge.",
    ["child", "state"],
)
CONTROL_PANEL_RUNTIME_CHILD_HEARTBEAT_AGE = Gauge(
    "evm_control_panel_runtime_child_heartbeat_age_seconds",
    "Age of the latest exact-identity child heartbeat.",
    ["child"],
)
CONTROL_PANEL_RUNTIME_CHILD_REVISION_MATCH = Gauge(
    "evm_control_panel_runtime_child_revision_match",
    "Whether child source revision matches the supervisor revision.",
    ["child"],
)
CONTROL_PANEL_RUNTIME_CHILD_PROCESS_COUNT = Gauge(
    "evm_control_panel_runtime_child_process_count",
    "Number of command-matched processes for a supervised child.",
    ["child"],
)
CONTROL_PANEL_RUNTIME_CHILD_RESTART_COUNT = Gauge(
    "evm_control_panel_runtime_child_restart_count",
    "Persistent restart attempt count for a supervised child.",
    ["child"],
)
CONTROL_PANEL_METRIC_REFRESH_SUCCESS = Gauge(
    "evm_control_panel_metric_refresh_success",
    "Whether the latest control-plane metric refresh completed successfully.",
)
_CONTROL_PANEL_METRIC_LOCK = threading.Lock()
OPERATIONAL_METRICS = OperationalMetrics()
OPERATIONAL_METRICS_PATH = Path(
    os.getenv(
        "EVM_OPERATIONAL_METRICS_PATH",
        f"{EVM_DATA_MOUNT_ROOT}/artifacts/operations/failure_scenarios/_latest/metrics.json",
    )
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


@asynccontextmanager
async def lifespan(_: FastAPI):
    configure_tracing(APP_NAME, service_version=runtime_service_version())
    store = get_transactional_store()
    if store.enabled:
        store.verify_task_queue_cutover(
            mode=admission_queue_mode(),
            config=load_admission_queue_config(),
        )
        sync_task_json_mirror_from_store()
        if not verify_task_json_mirror_parity()["matches"]:
            raise ControlPlaneParityError(
                "PostgreSQL task authority and rollback mirrors diverged at startup"
            )
    refresh_model_state()
    try:
        yield
    finally:
        await shutdown_gpu_batch_probe_executor()
        shutdown_capacity_probe_executor()
        shutdown_tracing()


app = FastAPI(
    title=APP_NAME,
    version="0.1.0",
    lifespan=lifespan,
    separate_input_output_schemas=False,
)
app.add_middleware(TaskIngressBodyLimitMiddleware)


@app.middleware("http")
async def measure_http_in_flight(request: Request, call_next):
    counted = False
    try:
        counted = API_DRAIN_CONTROLLER.enter(request.url.path)
    except ApiDrainingError:
        return JSONResponse(
            status_code=503,
            content={"detail": "api_replica_draining"},
            headers={"Retry-After": "1", "Connection": "close"},
        )
    snapshot = API_DRAIN_CONTROLLER.snapshot()
    HTTP_REQUESTS_IN_FLIGHT.set(snapshot.in_flight)
    HTTP_REQUESTS_PEAK_IN_FLIGHT.set(snapshot.peak_in_flight)
    try:
        return await call_next(request)
    finally:
        API_DRAIN_CONTROLLER.exit(counted)
        HTTP_REQUESTS_IN_FLIGHT.set(API_DRAIN_CONTROLLER.snapshot().in_flight)


@app.middleware("http")
async def propagate_w3c_trace_context(request: Request, call_next):
    incoming = request.headers.get("traceparent")
    tracestate = request.headers.get("tracestate")
    regenerated = False
    try:
        parent = W3CTraceContext.parse(incoming, tracestate=tracestate) if incoming else None
    except TraceContextError:
        parent = None
        regenerated = True
    started = time.perf_counter()
    with trace_span(
        f"{request.method} {request.url.path}",
        parent=parent,
        kind="server",
        attributes={
            "http.request.method": request.method,
            "url.path": request.url.path,
            "evm.stage": "api",
        },
    ) as active:
        try:
            response = await call_next(request)
        except Exception:
            elapsed_seconds = time.perf_counter() - started
            route = getattr(request.scope.get("route"), "path", "unmatched")
            HTTP_REQUEST_COUNT.labels(request.method, route, "5xx").inc()
            HTTP_REQUEST_LATENCY.labels(request.method, route).observe(elapsed_seconds)
            raise
        elapsed_seconds = time.perf_counter() - started
        elapsed_ms = elapsed_seconds * 1000
        route = getattr(request.scope.get("route"), "path", "unmatched")
        status_class = f"{response.status_code // 100}xx"
        HTTP_REQUEST_COUNT.labels(request.method, route, status_class).inc()
        HTTP_REQUEST_LATENCY.labels(request.method, route).observe(elapsed_seconds)
        active.set_attribute("http.response.status_code", response.status_code)
        active.set_attribute("evm.trace_context_regenerated", regenerated)
        response.headers["traceparent"] = active.context.traceparent
        if active.context.tracestate:
            response.headers["tracestate"] = active.context.tracestate
        response.headers["x-evm-trace-id"] = active.context.trace_id
        LOGGER.info(
            "http_request_completed method=%s path=%s status=%s elapsed_ms=%.3f "
            "trace_id=%s span_id=%s inbound_context_regenerated=%s",
            request.method,
            request.url.path,
            response.status_code,
            elapsed_ms,
            active.context.trace_id,
            active.context.span_id,
            regenerated,
        )
        return response


app.include_router(control_panel_router)
app.include_router(control_panel_tasks_router)
app.include_router(control_panel_commands_router)
app.include_router(control_panel_ct_router)
app.include_router(control_panel_deployments_router)
app.include_router(control_panel_experiments_router)
app.include_router(control_panel_governance_router)
app.include_router(control_panel_incidents_router)
app.include_router(control_panel_lifecycle_router)
app.include_router(control_panel_orchestrators_router)
app.include_router(control_panel_profiles_router)
app.include_router(control_panel_scenarios_router)
app.include_router(control_panel_workloads_router)
app.include_router(control_panel_runtime_router)
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


def _predict(
    model: LoadedModel, payload: PredictRequest
) -> tuple[str, float, dict[str, float], str]:
    if model.model_type == "image_feature_centroid":
        features, feature_source = _request_features(payload)
        result = predict_with_model(model.source_model, features)
        scores = {
            str(label): round(float(score), 6) for label, score in result.get("scores", {}).items()
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


@app.get("/health")
def health() -> dict[str, str]:
    REQUEST_COUNT.labels(endpoint="/health", status="ok").inc()
    return {"status": "ok", "service": APP_NAME}


@app.get("/ready")
def ready(response: Response) -> dict[str, Any]:
    from evm.control_panel.transactional_store import (
        ControlPlaneStoreError,
        get_transactional_store,
    )

    drain = API_DRAIN_CONTROLLER.snapshot()
    if drain.draining:
        response.status_code = 503
        REQUEST_COUNT.labels(endpoint="/ready", status="draining").inc()
        return {
            "status": "draining",
            "runtime_revision_matches": True,
            "model_loaded": MODEL_STATE is not None,
            "runtime": drain.public_dict(),
        }

    try:
        mlflow_response = requests.get(f"{MLFLOW_TRACKING_URI}/health", timeout=2)
        mlflow_ready = mlflow_response.ok
    except requests.RequestException:
        mlflow_ready = False

    model = refresh_model_state()
    model_loaded = model is not None
    desired_source_commit = os.getenv("GIT_COMMIT") or os.getenv("EVM_GIT_COMMIT") or None
    runtime_source_commit = runtime_service_version(default="unknown")
    runtime_revision_matches = bool(
        desired_source_commit
        and runtime_source_commit != "unknown"
        and desired_source_commit == runtime_source_commit
    )
    configured_store_mode = os.getenv("EVM_CONTROL_PLANE_STORE_MODE", "file").strip().lower()
    control_plane_store_required = configured_store_mode in {"dual", "postgres"}
    control_plane_store_ready = not control_plane_store_required
    control_plane_store_error: str | None = None
    try:
        control_plane_store = get_transactional_store()
        configured_store_mode = control_plane_store.mode
        if control_plane_store.enabled:
            control_plane_store.get_entity("readiness_probe", "readiness_probe")
            control_plane_store_ready = True
    except ControlPlaneStoreError as exc:
        control_plane_store_error = type(exc).__name__
    status = (
        "ok"
        if (
            mlflow_ready and model_loaded and runtime_revision_matches and control_plane_store_ready
        )
        else "degraded"
    )
    response.status_code = 200 if status == "ok" else 503
    REQUEST_COUNT.labels(endpoint="/ready", status=status).inc()
    payload: dict[str, Any] = {
        "status": status,
        "source_commit": desired_source_commit,
        "runtime_source_commit": runtime_source_commit,
        "runtime_revision_matches": runtime_revision_matches,
        "source_branch": os.getenv("GIT_BRANCH") or os.getenv("EVM_GIT_BRANCH") or None,
        "mlflow_tracking_uri": MLFLOW_TRACKING_URI,
        "mlflow_ready": mlflow_ready,
        "model_loaded": model_loaded,
        "model_load_error": MODEL_LOAD_ERROR,
        "control_plane_store_mode": configured_store_mode,
        "control_plane_store_required": control_plane_store_required,
        "control_plane_store_ready": control_plane_store_ready,
        "control_plane_store_error": control_plane_store_error,
        "runtime": drain.public_dict(),
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
    try:
        refresh_control_panel_metrics()
    except Exception:
        CONTROL_PANEL_METRIC_REFRESH_SUCCESS.set(0)
        LOGGER.exception("Control-plane Prometheus metric refresh failed")
    else:
        CONTROL_PANEL_METRIC_REFRESH_SUCCESS.set(1)
    if OPERATIONAL_METRICS_PATH.is_file():
        try:
            OPERATIONAL_METRICS.update(load_metric_projection(OPERATIONAL_METRICS_PATH))
        except Exception:
            LOGGER.exception("Operational reliability metric refresh failed")
    try:
        refresh_incident_plane_metrics()
    except Exception:
        LOGGER.exception("Guard incident metric refresh failed")
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


def refresh_control_panel_metrics() -> None:
    from collections import Counter as ValueCounter

    from apps.api.control_panel import model_candidate_catalog_snapshot
    from evm.control_panel.host_runtime import read_host_runtime_supervisor
    from evm.control_panel.lifecycle_runs import read_runs, read_worker_state
    from evm.control_panel.stage_handoffs import build_stage_handoff_catalog

    with _CONTROL_PANEL_METRIC_LOCK:
        runs = read_runs().runs
        run_counts = ValueCounter((run.state, run.execution_mode) for run in runs)
        stage_counts = ValueCounter(
            (stage.stage_id, stage.state, stage.runtime) for run in runs for stage in run.stages
        )
        handoffs = build_stage_handoff_catalog(limit=1000)
        handoff_counts = ValueCounter(item.bucket for item in handoffs.handoffs)
        candidates = model_candidate_catalog_snapshot(limit=1000)
        candidate_counts = ValueCounter(
            (item.architecture, item.status, str(item.selectable).lower())
            for item in candidates.candidates
        )

        CONTROL_PANEL_LIFECYCLE_RUNS.clear()
        CONTROL_PANEL_STAGES.clear()
        CONTROL_PANEL_STAGE_PROGRESS.clear()
        CONTROL_PANEL_HANDOFFS.clear()
        CONTROL_PANEL_MODEL_CANDIDATES.clear()
        CONTROL_PANEL_RUNTIME_CHILD_STATE.clear()
        CONTROL_PANEL_RUNTIME_CHILD_HEARTBEAT_AGE.clear()
        CONTROL_PANEL_RUNTIME_CHILD_REVISION_MATCH.clear()
        CONTROL_PANEL_RUNTIME_CHILD_PROCESS_COUNT.clear()
        CONTROL_PANEL_RUNTIME_CHILD_RESTART_COUNT.clear()
        for (state, execution_mode), value in run_counts.items():
            CONTROL_PANEL_LIFECYCLE_RUNS.labels(state=state, execution_mode=execution_mode).set(
                value
            )
        for (stage, state, runtime), value in stage_counts.items():
            CONTROL_PANEL_STAGES.labels(stage=stage, state=state, runtime=runtime).set(value)
        for run in runs[:25]:
            for stage in run.stages:
                CONTROL_PANEL_STAGE_PROGRESS.labels(run_id=run.run_id, stage=stage.stage_id).set(
                    stage.progress
                )
        for bucket, value in handoff_counts.items():
            CONTROL_PANEL_HANDOFFS.labels(bucket=bucket).set(value)
        for (architecture, status, selectable), value in candidate_counts.items():
            CONTROL_PANEL_MODEL_CANDIDATES.labels(
                architecture=architecture,
                status=status,
                selectable=selectable,
            ).set(value)
        CONTROL_PANEL_WORKER_ONLINE.set(1 if read_worker_state().status == "online" else 0)
        supervisor = read_host_runtime_supervisor()
        CONTROL_PANEL_RUNTIME_SUPERVISOR_HEALTHY.set(1 if supervisor.status == "healthy" else 0)
        for child in supervisor.children:
            CONTROL_PANEL_RUNTIME_CHILD_STATE.labels(child=child.name, state=child.status).set(1)
            if child.heartbeat_age_seconds is not None:
                CONTROL_PANEL_RUNTIME_CHILD_HEARTBEAT_AGE.labels(child=child.name).set(
                    child.heartbeat_age_seconds
                )
            CONTROL_PANEL_RUNTIME_CHILD_REVISION_MATCH.labels(child=child.name).set(
                1 if child.revision_matches else 0
            )
            CONTROL_PANEL_RUNTIME_CHILD_PROCESS_COUNT.labels(child=child.name).set(
                child.process_count
            )
            CONTROL_PANEL_RUNTIME_CHILD_RESTART_COUNT.labels(child=child.name).set(
                supervisor.restart_counts.get(child.name, 0)
            )
