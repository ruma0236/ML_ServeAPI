import os
import time
from typing import Any

import requests
from fastapi import FastAPI, Response
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest
from pydantic import BaseModel, Field


APP_NAME = os.getenv("APP_NAME", "enterprise-vision-mlops-api")
MLFLOW_TRACKING_URI = os.getenv("MLFLOW_TRACKING_URI", "http://localhost:5000")
MODEL_NAME = os.getenv("MODEL_NAME", "vision-baseline")
MODEL_STAGE = os.getenv("MODEL_STAGE", "Production")

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
    prediction: str
    confidence: float
    latency_ms: float
    placeholder: bool


app = FastAPI(title=APP_NAME, version="0.1.0")


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

    status = "ok" if mlflow_ready else "degraded"
    REQUEST_COUNT.labels(endpoint="/ready", status=status).inc()
    return {
        "status": status,
        "mlflow_tracking_uri": MLFLOW_TRACKING_URI,
        "mlflow_ready": mlflow_ready,
    }


@app.post("/predict", response_model=PredictResponse)
def predict(payload: PredictRequest) -> PredictResponse:
    start = time.perf_counter()

    # Placeholder inference keeps the serving contract stable until a registered
    # MLflow model is added in the training milestone.
    width = float(payload.features.get("width", 0) or 0)
    height = float(payload.features.get("height", 0) or 0)
    confidence = 0.51 if width and height else 0.5
    prediction = "normal" if (width * height) % 2 == 0 else "anomaly"

    latency_ms = (time.perf_counter() - start) * 1000
    REQUEST_LATENCY.labels(endpoint="/predict").observe(latency_ms / 1000)
    REQUEST_COUNT.labels(endpoint="/predict", status="ok").inc()

    return PredictResponse(
        model_name=MODEL_NAME,
        model_stage=MODEL_STAGE,
        prediction=prediction,
        confidence=confidence,
        latency_ms=round(latency_ms, 3),
        placeholder=True,
    )


@app.get("/metrics")
def metrics() -> Response:
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
