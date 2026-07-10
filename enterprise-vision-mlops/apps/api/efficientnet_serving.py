from __future__ import annotations

import hashlib
import os
import threading
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from evm.core.image_feature_model import resolve_image_path


APP_NAME = os.getenv("APP_NAME", "evm-b7-serving")
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
            "device": str(self.device),
            "cuda_available": self.device.type == "cuda",
        }


class InferenceRequest(BaseModel):
    image_uri: str = Field(description="Image path or file URI under the mounted data root.")


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


MODEL_RUNTIME: ModelRuntime | None = None
MODEL_LOAD_ERROR = "model_not_loaded"
MODEL_LOCK = threading.Lock()


def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fp:
        while chunk := fp.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def _build_model(architecture: str, class_count: int) -> Any:
    from torch import nn
    from torchvision import models

    if architecture != "efficientnet-b7":
        raise ValueError(f"serving contract only accepts efficientnet-b7, got {architecture}")
    model = models.efficientnet_b7(weights=None)
    in_features = model.classifier[-1].in_features
    model.classifier[-1] = nn.Linear(in_features, class_count)
    return model


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
        raise RuntimeError("CUDA is required by the W7 B7 serving acceptance policy")

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
    )


def refresh_model() -> ModelRuntime | None:
    global MODEL_LOAD_ERROR, MODEL_RUNTIME

    with MODEL_LOCK:
        try:
            MODEL_RUNTIME = load_model()
            MODEL_LOAD_ERROR = ""
        except Exception as exc:
            MODEL_RUNTIME = None
            MODEL_LOAD_ERROR = str(exc)
    return MODEL_RUNTIME


@asynccontextmanager
async def lifespan(_: FastAPI):
    refresh_model()
    yield


app = FastAPI(title=APP_NAME, version="0.1.0", lifespan=lifespan)


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

    with Image.open(image_path) as image:
        tensor = runtime.transform(image.convert("RGB")).unsqueeze(0).to(runtime.device)
    started = time.perf_counter()
    with torch.inference_mode():
        probabilities = torch.softmax(runtime.model(tensor), dim=1)[0]
        if runtime.device.type == "cuda":
            torch.cuda.synchronize()
    latency_ms = (time.perf_counter() - started) * 1000
    scores = {
        label: round(float(probabilities[index].detach().cpu().item()), 6)
        for index, label in enumerate(runtime.class_names)
    }
    prediction = max(scores, key=scores.get)
    return InferenceResponse(
        candidate_id=runtime.candidate_id,
        model_sha256=runtime.model_sha256,
        dataset_version=runtime.dataset_version,
        image_uri=payload.image_uri,
        prediction=prediction,
        confidence=scores[prediction],
        scores=scores,
        latency_ms=round(latency_ms, 3),
        device=str(runtime.device),
    )
