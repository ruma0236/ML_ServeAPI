from __future__ import annotations

import hashlib
import logging
import os
import threading
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    Info,
    generate_latest,
)
from pydantic import BaseModel, Field

from evm.core.image_feature_model import resolve_image_path
from evm.model_runtime.family_admission import (
    AdmissionCost,
    FamilyAdmissionController,
    FamilyAdmissionError,
    FamilyAdmissionLimits,
    request_json_bytes,
)
from evm.observability.otel import (
    configure_tracing,
    runtime_service_version,
    shutdown_tracing,
    trace_span,
)
from evm.observability.trace_context import TraceContextError, W3CTraceContext


APP_NAME = os.getenv("APP_NAME", "evm-b7-serving")
LOGGER = logging.getLogger(__name__)
MODEL_PATH = Path(
    os.getenv(
        "EVM_MODEL_PATH",
        "/mnt/evm-data/artifacts/w7/efficientnet/w7-efficientnet-real-test-matrix/"
        "effnet-b7-img600-finetune-adamw/model.pt",
    )
)
EXPECTED_MODEL_SHA256 = os.getenv("EVM_MODEL_SHA256", "").lower()
EXPECTED_CANDIDATE_ID = os.getenv(
    "EVM_MODEL_CANDIDATE_ID", "effnet-b7-img600-finetune-adamw"
)
EXPECTED_DATASET_VERSION = os.getenv("EVM_DATASET_VERSION", "visa-open-data-f1f1c9ee9922")
REQUIRE_CUDA = os.getenv("EVM_REQUIRE_CUDA", "true").lower() in {"1", "true", "yes"}
HOST_DATA_ROOT = os.getenv(
    "EVM_HOST_DATA_ROOT", "F:/EnterpriseMLOps_Data/enterprise-vision-mlops"
)
DATA_MOUNT_ROOT = os.getenv("EVM_DATA_MOUNT_ROOT", "/mnt/evm-data")


@dataclass
class ModelRuntime:
    model: Any
    transform: Any
    device: Any
    candidate_id: str
    architecture: str
    dataset_version: str
    class_names: list[str]
    input_size: int
    model_sha256: str
    decision_threshold: float

    def metadata(self) -> dict[str, Any]:
        return {
            "service": APP_NAME,
            "model_loaded": True,
            "candidate_id": self.candidate_id,
            "architecture": self.architecture,
            "dataset_version": self.dataset_version,
            "class_names": self.class_names,
            "input_size": self.input_size,
            "model_path": str(MODEL_PATH),
            "model_sha256": self.model_sha256,
            "decision_threshold": self.decision_threshold,
            "device": str(self.device),
            "cuda_available": self.device.type == "cuda",
            "admission": IMAGE_ADMISSION.snapshot(),
        }


class InferenceRequest(BaseModel):
    image_uri: str = Field(
        max_length=4096,
        description="Image path or file URI under the mounted data root.",
    )
    deadline_seconds: float | None = Field(default=None, gt=0, le=180)


class InferenceResponse(BaseModel):
    candidate_id: str
    model_sha256: str
    dataset_version: str
    image_uri: str
    prediction: str
    confidence: float
    scores: dict[str, float]
    latency_ms: float
    device: str
    decision_threshold: float
    operational_metrics: dict[str, float] = Field(default_factory=dict)


MODEL_RUNTIME: ModelRuntime | None = None
MODEL_LOAD_ERROR = "model_not_loaded"
MODEL_LOCK = threading.Lock()
SERVING_REGISTRY = CollectorRegistry()
MODEL_LOADED = Gauge(
    "evm_serving_model_loaded",
    "Whether the immutable EfficientNet model is loaded and ready.",
    registry=SERVING_REGISTRY,
)
MODEL_IDENTITY = Info(
    "evm_serving_model",
    "Identity of the active immutable EfficientNet model.",
    registry=SERVING_REGISTRY,
)
INFERENCE_REQUESTS = Counter(
    "evm_serving_inference_requests_total",
    "Completed EfficientNet inference requests.",
    labelnames=("prediction", "status"),
    registry=SERVING_REGISTRY,
)
INFERENCE_LATENCY = Histogram(
    "evm_serving_inference_latency_seconds",
    "EfficientNet model forward-pass latency in seconds.",
    buckets=(0.001, 0.0025, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0),
    registry=SERVING_REGISTRY,
)
IMAGE_ADMISSION = FamilyAdmissionController(
    FamilyAdmissionLimits.from_path("image"),
    registry=SERVING_REGISTRY,
)


def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fp:
        while chunk := fp.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def torchvision_builder_name(architecture: str) -> str:
    builders = {
        "efficientnet-b0": "efficientnet_b0",
        "efficientnet-b7": "efficientnet_b7",
    }
    try:
        return builders[architecture]
    except KeyError as exc:
        raise ValueError(f"unsupported EfficientNet architecture: {architecture}") from exc


def _build_model(architecture: str, class_count: int) -> Any:
    from torch import nn
    from torchvision import models

    model = getattr(models, torchvision_builder_name(architecture))(weights=None)
    in_features = model.classifier[-1].in_features
    model.classifier[-1] = nn.Linear(in_features, class_count)
    return model


def prediction_for_scores(
    scores: dict[str, float], decision_threshold: float
) -> tuple[str, float]:
    anomaly_score = float(scores.get("anomaly", 0.0))
    prediction = "anomaly" if anomaly_score >= decision_threshold else "normal"
    return prediction, float(scores[prediction])


def load_model() -> ModelRuntime:
    import torch
    from torchvision import transforms

    if not MODEL_PATH.exists():
        raise FileNotFoundError(f"model artifact not found: {MODEL_PATH}")
    actual_sha256 = sha256_file(MODEL_PATH)
    if not EXPECTED_MODEL_SHA256:
        raise RuntimeError("EVM_MODEL_SHA256 is required for immutable model identity")
    if actual_sha256 != EXPECTED_MODEL_SHA256:
        raise RuntimeError(
            f"model digest mismatch: expected={EXPECTED_MODEL_SHA256} actual={actual_sha256}"
        )
    if REQUIRE_CUDA and not torch.cuda.is_available():
        raise RuntimeError("CUDA is required by the W7 EfficientNet serving policy")

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    checkpoint = torch.load(MODEL_PATH, map_location=device, weights_only=True)
    candidate_id = str(checkpoint.get("candidate_id") or "")
    if candidate_id != EXPECTED_CANDIDATE_ID:
        raise RuntimeError(
            f"candidate mismatch: expected={EXPECTED_CANDIDATE_ID} actual={candidate_id}"
        )
    architecture = str(checkpoint.get("architecture") or "")
    dataset_version = str(checkpoint.get("dataset_version") or "")
    if dataset_version != EXPECTED_DATASET_VERSION:
        raise RuntimeError(
            f"dataset version mismatch: expected={EXPECTED_DATASET_VERSION} actual={dataset_version}"
        )
    class_names = [str(item) for item in checkpoint.get("class_names", [])]
    if not class_names:
        raise RuntimeError("checkpoint class_names are missing")
    input_size = int(checkpoint.get("input_size") or 0)
    if input_size <= 0:
        raise RuntimeError("checkpoint input_size is invalid")
    decision_threshold = float(checkpoint.get("decision_threshold", 0.5))
    if not 0.0 <= decision_threshold <= 1.0:
        raise RuntimeError("checkpoint decision_threshold is invalid")

    model = _build_model(architecture, len(class_names)).to(device)
    model.load_state_dict(checkpoint["state_dict"])
    model.eval()
    transform = transforms.Compose(
        [
            transforms.Resize((input_size, input_size)),
            transforms.ToTensor(),
            transforms.Normalize(
                mean=[0.485, 0.456, 0.406],
                std=[0.229, 0.224, 0.225],
            ),
        ]
    )
    return ModelRuntime(
        model=model,
        transform=transform,
        device=device,
        candidate_id=candidate_id,
        architecture=architecture,
        dataset_version=dataset_version,
        class_names=class_names,
        input_size=input_size,
        model_sha256=actual_sha256,
        decision_threshold=decision_threshold,
    )


def refresh_model() -> ModelRuntime | None:
    global MODEL_LOAD_ERROR, MODEL_RUNTIME

    with MODEL_LOCK:
        try:
            MODEL_RUNTIME = load_model()
            MODEL_LOAD_ERROR = ""
            MODEL_LOADED.set(1)
            MODEL_IDENTITY.info(
                {
                    "candidate_id": MODEL_RUNTIME.candidate_id,
                    "architecture": MODEL_RUNTIME.architecture,
                    "dataset_version": MODEL_RUNTIME.dataset_version,
                    "model_sha256": MODEL_RUNTIME.model_sha256,
                    "device": str(MODEL_RUNTIME.device),
                }
            )
        except Exception as exc:
            MODEL_RUNTIME = None
            MODEL_LOAD_ERROR = str(exc)
            MODEL_LOADED.set(0)
    return MODEL_RUNTIME


@asynccontextmanager
async def lifespan(_: FastAPI):
    configure_tracing(APP_NAME, service_version=runtime_service_version())
    refresh_model()
    try:
        yield
    finally:
        shutdown_tracing()


app = FastAPI(title=APP_NAME, version="0.1.0", lifespan=lifespan)


@app.middleware("http")
async def propagate_serving_trace_context(request: Request, call_next):
    try:
        parent = (
            W3CTraceContext.parse(
                request.headers["traceparent"],
                tracestate=request.headers.get("tracestate"),
            )
            if "traceparent" in request.headers
            else None
        )
    except TraceContextError:
        parent = None
    with trace_span(
        f"{request.method} {request.url.path}",
        parent=parent,
        kind="server",
        attributes={
            "http.request.method": request.method,
            "url.path": request.url.path,
            "evm.stage": "serving",
        },
    ) as active:
        response = await call_next(request)
        active.set_attribute("http.response.status_code", response.status_code)
        response.headers["traceparent"] = active.context.traceparent
        response.headers["x-evm-trace-id"] = active.context.trace_id
        LOGGER.info(
            "serving_request_completed method=%s path=%s status=%s trace_id=%s span_id=%s",
            request.method,
            request.url.path,
            response.status_code,
            active.context.trace_id,
            active.context.span_id,
        )
        return response


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": APP_NAME}


@app.get("/ready")
def ready() -> JSONResponse:
    runtime = MODEL_RUNTIME
    if runtime is None:
        return JSONResponse(
            status_code=503,
            content={
                "status": "blocked",
                "service": APP_NAME,
                "model_loaded": False,
                "require_cuda": REQUIRE_CUDA,
                "error": MODEL_LOAD_ERROR,
            },
        )
    return JSONResponse(status_code=200, content={"status": "ok", **runtime.metadata()})


@app.get("/metadata")
def metadata() -> dict[str, Any]:
    runtime = MODEL_RUNTIME
    if runtime is None:
        raise HTTPException(status_code=503, detail=MODEL_LOAD_ERROR)
    return runtime.metadata()


@app.get("/metrics")
def metrics() -> Response:
    return Response(
        content=generate_latest(SERVING_REGISTRY),
        media_type=CONTENT_TYPE_LATEST,
    )


@app.post("/predict", response_model=InferenceResponse)
def predict(payload: InferenceRequest) -> InferenceResponse:
    import torch
    from PIL import Image

    runtime = MODEL_RUNTIME
    if runtime is None:
        raise HTTPException(status_code=503, detail=MODEL_LOAD_ERROR)
    image_path = resolve_image_path(
        payload.image_uri,
        host_data_root=HOST_DATA_ROOT,
        data_mount_root=DATA_MOUNT_ROOT,
    )
    if image_path is None or not image_path.exists():
        raise HTTPException(
            status_code=422,
            detail={"error": "image is not readable", "resolved_path": str(image_path or "")},
        )

    with Image.open(image_path) as image_header:
        width, height = image_header.size
    cost = AdmissionCost(
        request_bytes=request_json_bytes(payload),
        image_bytes=image_path.stat().st_size,
        image_pixels=width * height,
    )
    try:
        with IMAGE_ADMISSION.acquire(
            cost,
            deadline_seconds=payload.deadline_seconds,
        ) as lease:
            decode_started = time.perf_counter()
            with Image.open(image_path) as image:
                rgb_image = image.convert("RGB")
            decode_seconds = time.perf_counter() - decode_started
            preprocess_started = time.perf_counter()
            tensor = runtime.transform(rgb_image).unsqueeze(0).to(runtime.device)
            preprocess_seconds = time.perf_counter() - preprocess_started
            if runtime.device.type == "cuda":
                torch.cuda.reset_peak_memory_stats(runtime.device)
            started = time.perf_counter()
            with torch.inference_mode():
                probabilities = torch.softmax(runtime.model(tensor), dim=1)[0]
                if runtime.device.type == "cuda":
                    torch.cuda.synchronize()
            inference_seconds = time.perf_counter() - started
            peak_vram_bytes = (
                int(torch.cuda.max_memory_allocated(runtime.device))
                if runtime.device.type == "cuda"
                else 0
            )
            operational_metrics = {
                "request_bytes": float(cost.request_bytes),
                "image_bytes": float(cost.image_bytes),
                "image_pixels": float(cost.image_pixels),
                "queue_wait_seconds": lease.queue_wait_seconds,
                "decode_seconds": decode_seconds,
                "preprocess_seconds": preprocess_seconds,
                "inference_seconds": inference_seconds,
                "peak_vram_bytes": float(peak_vram_bytes),
            }
            IMAGE_ADMISSION.record_runtime_metrics(operational_metrics)
    except FamilyAdmissionError as exc:
        headers = (
            {"Retry-After": str(exc.retry_after_seconds)}
            if exc.retry_after_seconds is not None
            else None
        )
        raise HTTPException(
            status_code=exc.status_code,
            detail=exc.code,
            headers=headers,
        ) from exc
    latency_ms = inference_seconds * 1000
    scores = {
        label: round(float(probabilities[index].detach().cpu().item()), 6)
        for index, label in enumerate(runtime.class_names)
    }
    prediction, confidence = prediction_for_scores(scores, runtime.decision_threshold)
    INFERENCE_REQUESTS.labels(prediction=prediction, status="success").inc()
    INFERENCE_LATENCY.observe(latency_ms / 1000)
    return InferenceResponse(
        candidate_id=runtime.candidate_id,
        model_sha256=runtime.model_sha256,
        dataset_version=runtime.dataset_version,
        image_uri=payload.image_uri,
        prediction=prediction,
        confidence=confidence,
        scores=scores,
        latency_ms=round(latency_ms, 3),
        device=str(runtime.device),
        decision_threshold=runtime.decision_threshold,
        operational_metrics={
            key: round(float(value), 6) for key, value in operational_metrics.items()
        },
    )
