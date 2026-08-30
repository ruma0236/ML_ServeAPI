from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import threading
import time
from pathlib import Path
from typing import Any, Awaitable, Callable, Literal, Mapping

import httpx
from opentelemetry import trace as otel_trace
from prometheus_client import Counter, Gauge, Histogram
from pydantic import BaseModel, Field, field_validator, model_validator

from evm.scale_validation.x1_contract import MODEL_IDS, canonical_sha256, sha256_file
from evm.scale_validation.x1_runtime import (
    X1RuntimeValidationError,
    normalize_triton_repository_index,
    validate_triton_runtime_config,
)


_TRACEPARENT = re.compile(r"^00-([a-f0-9]{32})-([a-f0-9]{16})-(0[01])$")

REQUESTS = Counter(
    "evm_x1_requests_total",
    "Canonical X1 requests by model and terminal result.",
    ("model_id", "result"),
)
LATENCY = Histogram(
    "evm_x1_request_seconds",
    "Canonical X1 end-to-end request latency.",
    ("model_id",),
)
QUEUE_WAIT = Histogram(
    "evm_x1_queue_wait_seconds",
    "Canonical X1 process-local admission wait.",
    ("model_id",),
)
ACTIVE = Gauge(
    "evm_x1_active_requests",
    "Canonical X1 active requests in this API worker.",
    ("model_id", "worker_slot"),
)


class X1ServingError(RuntimeError):
    def __init__(self, code: str, detail: str, *, status_code: int = 409) -> None:
        super().__init__(detail)
        self.code = code
        self.status_code = status_code


class X1InferenceRequest(BaseModel):
    schema_version: Literal["evm.s8_v4.x1_inference_request.v1"]
    suite_id: str = Field(pattern=r"^x1-[a-z0-9-]{8,120}$")
    attempt_id: str = Field(pattern=r"^x1-[a-z0-9-]{8,160}$")
    request_id: str = Field(pattern=r"^x1-[a-z0-9-]{8,200}$")
    traceparent: str
    model_id: Literal[
        "higgs_logistic_regression",
        "higgs_gaussian_nb",
        "higgs_tiny_mlp",
        "criteo_dlrm_lite",
    ]
    model_version: Literal["1"]
    artifact_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    config_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    features: list[float]
    deadline_unix_ns: int = Field(gt=0)
    lease_id: str = Field(min_length=8, max_length=200)
    fencing_token: str = Field(min_length=16, max_length=512)

    @field_validator("traceparent")
    @classmethod
    def validate_traceparent(cls, value: str) -> str:
        if _TRACEPARENT.fullmatch(value) is None:
            raise ValueError("invalid W3C traceparent")
        return value

    @field_validator("features")
    @classmethod
    def validate_features(cls, value: list[float]) -> list[float]:
        if not value or any(
            not isinstance(item, float) or not (-1e12 < item < 1e12) for item in value
        ):
            raise ValueError("features must be finite float values")
        return value

    @model_validator(mode="after")
    def validate_feature_count(self) -> "X1InferenceRequest":
        expected = 39 if self.model_id == "criteo_dlrm_lite" else 28
        if len(self.features) != expected:
            raise ValueError(f"{self.model_id} requires {expected} features")
        return self


class X1TopologyIdentity(BaseModel):
    pod_uid: str
    pod_name: str
    service_instance_id: str
    worker_pid: int = Field(gt=0)
    worker_thread_id: int = Field(gt=0)
    worker_slot: str
    api_replicas_expected: int = Field(ge=1, le=2)
    cpu_workers_expected: int = Field(ge=1, le=4)


class X1DurableEffect(BaseModel):
    effect_id: str
    replayed: bool
    committed: bool
    readback_visible: bool
    receipt: dict[str, Any]


class X1InferenceResponse(BaseModel):
    schema_version: Literal["evm.s8_v4.x1_inference_response.v1"]
    suite_id: str
    attempt_id: str
    request_id: str
    trace_id: str
    model_id: str
    model_version: str
    artifact_sha256: str
    config_sha256: str
    runtime_device: Literal["cuda"]
    triton_instance_kind: Literal["KIND_GPU"]
    triton_instance_count: Literal[1]
    triton_gpu_device: Literal[0]
    output: list[float]
    result_sha256: str
    topology: X1TopologyIdentity
    queue_wait_ms: float = Field(ge=0)
    prediction_ms: float = Field(ge=0)
    total_ms: float = Field(ge=0)
    terminal_outcome: Literal["completed"]
    effect_id: str
    durable_effect: X1DurableEffect | None = None


TerminalCommitter = Callable[
    [X1InferenceRequest, X1InferenceResponse], Awaitable[Mapping[str, Any]]
]


class X1ServingManager:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._manifest: dict[str, Any] | None = None
        self._client: httpx.AsyncClient | None = None
        self._semaphore: asyncio.Semaphore | None = None
        self._validated_models: set[str] = set()

    async def close(self) -> None:
        client = self._client
        self._client = None
        if client is not None:
            await client.aclose()

    async def readiness(self) -> dict[str, Any]:
        manifest = self._load_manifest()
        models = manifest.get("models")
        if not isinstance(models, Mapping) or set(models) != set(MODEL_IDS):
            raise X1ServingError("x1_manifest_model_set", "readiness", status_code=503)
        try:
            async with self._new_http_client() as client:
                for model_id in MODEL_IDS:
                    await self._assert_triton_gpu_config(model_id, client=client, cache=False)
                index = await client.post("/v2/repository/index", json={}, timeout=5)
                index.raise_for_status()
                normalize_triton_repository_index(index.json())
        except httpx.HTTPError as exc:
            raise X1ServingError(
                "x1_triton_connection_unavailable",
                type(exc).__name__,
                status_code=503,
            ) from exc
        except X1RuntimeValidationError as exc:
            raise X1ServingError(str(exc), "readiness", status_code=503) from exc
        self._validated_models.update(MODEL_IDS)
        topology = _topology_identity()
        return {
            "schema_version": "evm.s8_v4.x1_readiness.v1",
            "status": "ok",
            "source_revision": manifest.get("source_revision"),
            "active_profile": manifest.get("active_profile"),
            "runtime_identity_sha256": manifest.get("runtime_identity_sha256"),
            "model_count": len(MODEL_IDS),
            "runtime_device": "cuda",
            "lease_id": manifest.get("lease", {}).get("lease_id"),
            "topology": topology.model_dump(mode="json"),
        }

    async def predict(
        self,
        request: X1InferenceRequest,
        *,
        terminal_committer: TerminalCommitter,
    ) -> X1InferenceResponse:
        self._assert_lease(request)
        identity = self._model_identity(request.model_id)
        if (
            request.model_version != identity["model_version"]
            or request.artifact_sha256 != identity["artifact_sha256"]
            or request.config_sha256 != identity["config_sha256"]
        ):
            raise X1ServingError("x1_model_identity_mismatch", request.request_id)
        now = time.time_ns()
        if now >= request.deadline_unix_ns:
            raise X1ServingError("x1_deadline_expired", request.request_id, status_code=408)
        semaphore = self._admission_semaphore()
        wait_started = time.perf_counter_ns()
        timeout = min(
            float(os.getenv("EVM_X1_ADMISSION_WAIT_SECONDS", "0.05")),
            max(0.0, (request.deadline_unix_ns - now) / 1_000_000_000),
        )
        try:
            await asyncio.wait_for(semaphore.acquire(), timeout=timeout)
        except TimeoutError as exc:
            REQUESTS.labels(request.model_id, "rejected").inc()
            raise X1ServingError(
                "x1_admission_rejected", request.request_id, status_code=429
            ) from exc
        queue_wait_ms = (time.perf_counter_ns() - wait_started) / 1_000_000
        topology = _topology_identity()
        worker_slot = topology.worker_slot
        ACTIVE.labels(request.model_id, worker_slot).inc()
        started = time.perf_counter_ns()
        try:
            await self._assert_triton_gpu_config(request.model_id)
            try:
                output, prediction_ms = await self._triton_predict(request)
            except X1ServingError:
                raise
            except Exception as exc:
                raise X1ServingError("x1_triton_request_failed", str(exc), status_code=503) from exc
            effect_id = hashlib.sha256(
                f"{request.attempt_id}:{request.request_id}".encode("utf-8")
            ).hexdigest()
            result_sha256 = canonical_sha256(
                {
                    "request_id": request.request_id,
                    "model_id": request.model_id,
                    "model_version": request.model_version,
                    "artifact_sha256": request.artifact_sha256,
                    "output": output,
                }
            )
            trace_match = _TRACEPARENT.fullmatch(request.traceparent)
            if trace_match is None:
                raise X1ServingError("x1_trace_identity", request.request_id)
            response = X1InferenceResponse(
                schema_version="evm.s8_v4.x1_inference_response.v1",
                suite_id=request.suite_id,
                attempt_id=request.attempt_id,
                request_id=request.request_id,
                trace_id=trace_match.group(1),
                model_id=request.model_id,
                model_version=request.model_version,
                artifact_sha256=request.artifact_sha256,
                config_sha256=request.config_sha256,
                runtime_device="cuda",
                triton_instance_kind="KIND_GPU",
                triton_instance_count=1,
                triton_gpu_device=0,
                output=output,
                result_sha256=result_sha256,
                topology=topology,
                queue_wait_ms=queue_wait_ms,
                prediction_ms=prediction_ms,
                total_ms=(time.perf_counter_ns() - started) / 1_000_000,
                terminal_outcome="completed",
                effect_id=effect_id,
            )
            try:
                receipt = dict(await terminal_committer(request, response))
            except X1ServingError:
                raise
            except Exception as exc:
                raise X1ServingError(
                    "x1_durable_effect_commit_failed", str(exc), status_code=503
                ) from exc
            if (
                receipt.get("committed") is not True
                or receipt.get("readback_visible") is not True
                or receipt.get("effect_id") != effect_id
            ):
                raise X1ServingError("x1_durable_effect_unconfirmed", request.request_id)
            response.durable_effect = X1DurableEffect(
                effect_id=effect_id,
                replayed=bool(receipt.get("replayed", False)),
                committed=True,
                readback_visible=True,
                receipt=receipt,
            )
            span = otel_trace.get_current_span()
            span.set_attributes(
                {
                    "evm.x1.suite_id": request.suite_id,
                    "evm.x1.attempt_id": request.attempt_id,
                    "evm.x1.request_id": request.request_id,
                    "evm.x1.model_id": request.model_id,
                    "evm.x1.model_version": request.model_version,
                    "evm.x1.artifact_sha256": request.artifact_sha256,
                    "evm.x1.effect_id": effect_id,
                    "evm.x1.runtime_device": "cuda",
                    "evm.x1.pod_uid": topology.pod_uid,
                    "evm.x1.worker_pid": topology.worker_pid,
                    "evm.terminal.outcome": "completed",
                }
            )
            REQUESTS.labels(request.model_id, "completed").inc()
            LATENCY.labels(request.model_id).observe(response.total_ms / 1000)
            QUEUE_WAIT.labels(request.model_id).observe(queue_wait_ms / 1000)
            return response
        except X1ServingError:
            REQUESTS.labels(request.model_id, "failed").inc()
            raise
        except Exception as exc:
            REQUESTS.labels(request.model_id, "failed").inc()
            raise X1ServingError("x1_request_processing_failed", str(exc), status_code=503) from exc
        finally:
            ACTIVE.labels(request.model_id, worker_slot).dec()
            semaphore.release()

    async def _assert_triton_gpu_config(
        self,
        model_id: str,
        *,
        client: httpx.AsyncClient | None = None,
        cache: bool = True,
    ) -> None:
        if cache and model_id in self._validated_models:
            return
        identity = self._model_identity(model_id)
        response = await (client or self._http_client()).get(
            f"/v2/models/{model_id}/versions/1/config",
            timeout=5,
        )
        response.raise_for_status()
        payload = response.json()
        try:
            validate_triton_runtime_config(payload, model_id=model_id, identity=identity)
        except RuntimeError as exc:
            raise X1ServingError("x1_triton_gpu_config", model_id, status_code=503) from exc
        if cache:
            self._validated_models.add(model_id)

    async def _triton_predict(self, request: X1InferenceRequest) -> tuple[list[float], float]:
        manifest = self._load_manifest()
        profile = str(manifest.get("active_profile") or "disabled")
        max_batch_size = int(
            manifest["profiles"][profile]["models"][request.model_id]["max_batch_size"]
        )
        shape = [len(request.features)] if max_batch_size == 0 else [1, len(request.features)]
        payload = {
            "id": request.request_id,
            "inputs": [
                {
                    "name": "INPUT__0",
                    "shape": shape,
                    "datatype": "FP32",
                    "data": request.features,
                }
            ],
            "outputs": [{"name": "OUTPUT__0"}],
        }
        started = time.perf_counter_ns()
        response = await self._http_client().post(
            f"/v2/models/{request.model_id}/versions/1/infer",
            json=payload,
            timeout=max(0.001, (request.deadline_unix_ns - time.time_ns()) / 1_000_000_000),
        )
        response.raise_for_status()
        prediction_ms = (time.perf_counter_ns() - started) / 1_000_000
        body = response.json()
        outputs = body.get("outputs")
        if not isinstance(outputs, list) or len(outputs) != 1:
            raise X1ServingError("x1_triton_output_schema", request.request_id)
        record = outputs[0]
        if record.get("name") != "OUTPUT__0" or record.get("datatype") != "FP32":
            raise X1ServingError("x1_triton_output_identity", request.request_id)
        data = record.get("data")
        if not isinstance(data, list) or len(data) != 1:
            raise X1ServingError("x1_triton_output_cardinality", request.request_id)
        value = data[0]
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise X1ServingError("x1_triton_output_type", request.request_id)
        return [float(value)], prediction_ms

    def _model_identity(self, model_id: str) -> Mapping[str, Any]:
        manifest = self._load_manifest()
        models = manifest.get("models")
        if not isinstance(models, Mapping) or set(models) != set(MODEL_IDS):
            raise X1ServingError("x1_manifest_model_set", model_id, status_code=503)
        identity = models.get(model_id)
        if not isinstance(identity, Mapping):
            raise X1ServingError("x1_manifest_model_identity", model_id, status_code=503)
        return identity

    def _load_manifest(self) -> dict[str, Any]:
        observed = self._manifest
        if observed is not None:
            return observed
        with self._lock:
            if self._manifest is not None:
                return self._manifest
            path = Path(os.getenv("EVM_X1_RUNTIME_MANIFEST", "")).resolve()
            if not path.is_file():
                raise X1ServingError("x1_runtime_manifest_missing", str(path), status_code=503)
            payload = json.loads(path.read_bytes())
            if payload.get("schema_version") != "evm.s8_v4.x1_runtime_manifest.v1":
                raise X1ServingError("x1_runtime_manifest_schema", str(path), status_code=503)
            artifact_path = Path(str(payload.get("artifact_manifest_path")))
            if payload.get("manifest_sha256") != sha256_file(artifact_path):
                raise X1ServingError("x1_artifact_manifest_binding", str(path), status_code=503)
            self._manifest = payload
            return payload

    def _http_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = self._new_http_client()
        return self._client

    @staticmethod
    def _new_http_client() -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=os.getenv("EVM_X1_TRITON_URL", "http://evm-x1-triton:8000"),
            limits=httpx.Limits(max_connections=128, max_keepalive_connections=64),
            trust_env=False,
        )

    def _admission_semaphore(self) -> asyncio.Semaphore:
        if self._semaphore is None:
            value = int(os.getenv("EVM_X1_MAX_OUTSTANDING_PER_WORKER", "32"))
            if value < 1 or value > 512:
                raise X1ServingError("x1_admission_bound", str(value), status_code=503)
            self._semaphore = asyncio.Semaphore(value)
        return self._semaphore

    @staticmethod
    def _assert_lease(request: X1InferenceRequest) -> None:
        if request.lease_id != os.getenv(
            "EVM_X1_LEASE_ID", ""
        ) or request.fencing_token != os.getenv("EVM_X1_FENCING_TOKEN", ""):
            raise X1ServingError("x1_gpu_lease_identity", request.request_id)


def _topology_identity() -> X1TopologyIdentity:
    pod_uid = os.getenv("EVM_POD_UID", "").strip()
    pod_name = os.getenv("EVM_POD_NAME", "").strip()
    service_instance = os.getenv("OTEL_SERVICE_INSTANCE_ID", "").strip()
    replicas = int(os.getenv("EVM_X1_API_REPLICAS", "0"))
    workers = int(os.getenv("EVM_X1_CPU_WORKERS", "0"))
    if (
        not pod_uid
        or not pod_name
        or not service_instance
        or replicas not in {1, 2}
        or workers not in {1, 2, 4}
    ):
        raise X1ServingError("x1_topology_identity_missing", pod_name, status_code=503)
    pid = os.getpid()
    thread_id = threading.get_native_id()
    return X1TopologyIdentity(
        pod_uid=pod_uid,
        pod_name=pod_name,
        service_instance_id=service_instance,
        worker_pid=pid,
        worker_thread_id=thread_id,
        worker_slot=f"{pod_uid}:{pid}",
        api_replicas_expected=replicas,
        cpu_workers_expected=workers,
    )


manager = X1ServingManager()
