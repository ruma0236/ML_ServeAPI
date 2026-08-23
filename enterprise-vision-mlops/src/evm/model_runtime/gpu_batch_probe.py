from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from prometheus_client import Counter, Gauge, Histogram

from evm.control_panel.scenario_workloads import (
    GpuBatchProbeDescriptor,
    GpuBatchProbeRequest,
    GpuBatchProbeResponse,
    GpuBatchProbeRuntime,
    GpuBatchProbeTimings,
    ScenarioWorkloadError,
    assert_scale_validation_gpu_lease_owner,
)
from evm.observability.otel import trace_span
from evm.observability.trace_context import W3CTraceContext, current_trace_context
from evm.model_runtime.tiny_mlp import build_tiny_mlp


GPU_BATCH_ADMISSION_TOTAL = Counter(
    "evm_s4_gpu_batch_admission_total",
    "S4 GPU batching admission outcomes by bounded reason.",
    ("outcome", "reason"),
)
GPU_BATCH_QUEUE_DEPTH = Gauge(
    "evm_s4_gpu_batch_queue_depth",
    "S4 requests waiting in the bounded process-local GPU queue.",
)
GPU_BATCH_QUEUE_BYTES = Gauge(
    "evm_s4_gpu_batch_queue_bytes",
    "Canonical bytes retained by the S4 process-local GPU queue.",
)
GPU_BATCH_IN_FLIGHT = Gauge(
    "evm_s4_gpu_batch_in_flight",
    "S4 requests included in GPU batches currently executing.",
)
GPU_BATCH_FORMED_SIZE = Histogram(
    "evm_s4_gpu_batch_formed_size",
    "Observed S4 formed batch sizes.",
    buckets=(1, 2, 4, 8, 16, 32, 64),
)
GPU_BATCH_QUEUE_WAIT_SECONDS = Histogram(
    "evm_s4_gpu_batch_queue_wait_seconds",
    "S4 request wait from admission to GPU batch execution.",
    buckets=(0.0001, 0.0005, 0.001, 0.002, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5),
)
GPU_BATCH_STAGE_SECONDS = Histogram(
    "evm_s4_gpu_batch_stage_seconds",
    "S4 GPU batch stage latency.",
    ("stage",),
    buckets=(0.00001, 0.00005, 0.0001, 0.0005, 0.001, 0.002, 0.005, 0.01, 0.05, 0.1),
)
GPU_BATCH_VRAM_BYTES = Gauge(
    "evm_s4_gpu_batch_vram_bytes",
    "S4 Torch CUDA memory by bounded memory kind.",
    ("kind",),
)
GPU_BATCH_INSTANCE_IN_FLIGHT = Gauge(
    "evm_s4_gpu_batch_instance_in_flight",
    "S4 GPU batch execution state per bounded model instance.",
    ("instance_id",),
)


class GpuBatchProbeError(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        status_code: int = 409,
        headers: dict[str, str] | None = None,
    ):
        super().__init__(message)
        self.code = code
        self.status_code = status_code
        self.headers = headers or {}


@dataclass(frozen=True)
class GpuBatchExecutionConfig:
    enabled: bool
    registry_path: Path
    batch_size: int
    max_delay_ms: int
    instance_count: int
    max_outstanding: int
    max_outstanding_bytes: int
    max_request_bytes: int
    admission_wait_seconds: float
    request_timeout_seconds: float
    retry_after_seconds: int
    lease_run_id: str
    lease_id: str
    lease_fencing_token: str

    @classmethod
    def from_environment(cls) -> "GpuBatchExecutionConfig":
        config = cls(
            enabled=_environment_bool("EVM_S4_GPU_BATCH_ENABLED", False),
            registry_path=Path(os.getenv("EVM_S4_GPU_BATCH_REGISTRY", "").strip()),
            batch_size=_environment_int("EVM_S4_GPU_BATCH_SIZE", 1, 1, 32),
            max_delay_ms=_environment_int("EVM_S4_GPU_BATCH_MAX_DELAY_MS", 0, 0, 10),
            instance_count=_environment_int("EVM_S4_GPU_INSTANCE_COUNT", 1, 1, 2),
            max_outstanding=_environment_int("EVM_S4_GPU_MAX_OUTSTANDING", 256, 1, 4096),
            max_outstanding_bytes=_environment_int(
                "EVM_S4_GPU_MAX_OUTSTANDING_BYTES", 4 * 1024 * 1024, 1024, 256 * 1024 * 1024
            ),
            max_request_bytes=_environment_int(
                "EVM_S4_GPU_MAX_REQUEST_BYTES", 8192, 256, 1024 * 1024
            ),
            admission_wait_seconds=_environment_float(
                "EVM_S4_GPU_ADMISSION_WAIT_SECONDS", 0.05, 0, 30
            ),
            request_timeout_seconds=_environment_float(
                "EVM_S4_GPU_REQUEST_TIMEOUT_SECONDS", 5.0, 0.1, 300
            ),
            retry_after_seconds=_environment_int("EVM_S4_GPU_RETRY_AFTER_SECONDS", 1, 1, 3600),
            lease_run_id=os.getenv("EVM_S4_GPU_LEASE_RUN_ID", "").strip(),
            lease_id=os.getenv("EVM_S4_GPU_LEASE_ID", "").strip(),
            lease_fencing_token=os.getenv("EVM_S4_GPU_LEASE_FENCING_TOKEN", "").strip(),
        )
        if config.enabled:
            if not config.registry_path.is_file():
                raise GpuBatchProbeError(
                    "gpu_batch_registry_missing",
                    "The governed S4 model registry is unavailable.",
                    status_code=503,
                )
            if not all((config.lease_run_id, config.lease_id, config.lease_fencing_token)):
                raise GpuBatchProbeError(
                    "gpu_batch_lease_binding_missing",
                    "The S4 runtime requires an exact GPU lease binding.",
                    status_code=503,
                )
            if config.max_outstanding < config.batch_size * config.instance_count:
                raise GpuBatchProbeError(
                    "gpu_batch_config_invalid",
                    "Outstanding capacity must cover one batch per model instance.",
                    status_code=503,
                )
            if config.max_outstanding_bytes < config.max_request_bytes:
                raise GpuBatchProbeError(
                    "gpu_batch_config_invalid",
                    "Outstanding byte capacity must cover one request.",
                    status_code=503,
                )
        return config


@dataclass(frozen=True)
class BatchInferenceResult:
    probabilities: list[float]
    h2d_ms: float
    inference_ms: float
    d2h_ms: float
    allocated_vram_bytes: int
    reserved_vram_bytes: int
    peak_vram_bytes: int


class GpuBatchBackend(Protocol):
    descriptor: GpuBatchProbeDescriptor

    def infer(self, features: list[list[float]], instance_id: int) -> BatchInferenceResult: ...


@dataclass
class _PendingRequest:
    request: GpuBatchProbeRequest
    future: asyncio.Future[GpuBatchProbeResponse]
    request_bytes: int
    admitted_at: float
    admission_wait_seconds: float
    parent_trace: W3CTraceContext | None


class TorchGpuBatchBackend:
    def __init__(self, config: GpuBatchExecutionConfig):
        try:
            import torch
        except ImportError as exc:
            raise GpuBatchProbeError(
                "gpu_batch_torch_unavailable",
                "Torch is unavailable in this API runtime profile.",
                status_code=503,
            ) from exc
        if not torch.cuda.is_available() or torch.cuda.device_count() != 1:
            raise GpuBatchProbeError(
                "gpu_batch_cuda_identity_invalid",
                "S4 requires exactly one visible CUDA accelerator.",
                status_code=503,
            )
        registry = _load_registry(config.registry_path)
        artifact_path = _resolve_artifact(config.registry_path, registry["artifact_uri"])
        artifact_sha = _file_sha256(artifact_path)
        if artifact_sha != registry["artifact_sha256"]:
            raise GpuBatchProbeError(
                "gpu_batch_artifact_digest_mismatch",
                "S4 model artifact does not match the governed registry.",
                status_code=503,
            )
        checkpoint = torch.load(artifact_path, map_location="cpu", weights_only=True)
        if not isinstance(checkpoint, dict) or "state_dict" not in checkpoint:
            raise GpuBatchProbeError(
                "gpu_batch_artifact_contract_invalid",
                "S4 model artifact is not a supported state dictionary.",
                status_code=503,
            )

        self._torch = torch
        self._device = torch.device("cuda:0")
        self._models = []
        self._streams = []
        for _ in range(config.instance_count):
            model = build_tiny_mlp(torch).to(self._device, dtype=torch.float32)
            model.load_state_dict(checkpoint["state_dict"])
            model.eval()
            self._models.append(model)
            self._streams.append(torch.cuda.Stream(device=self._device))
        self._mean = torch.tensor(
            registry["preprocessing"]["mean"], device=self._device, dtype=torch.float32
        )
        self._scale = torch.tensor(
            registry["preprocessing"]["scale"], device=self._device, dtype=torch.float32
        )
        self.descriptor = GpuBatchProbeDescriptor(
            dataset_version=registry["dataset_version"],
            dataset_identity_sha256=registry["dataset_identity_sha256"],
            split_manifest_sha256=registry["split_manifest_sha256"],
            model_identity_sha256=registry["model_identity_sha256"],
            artifact_sha256=registry["artifact_sha256"],
            framework=registry["framework"]["torch"],
            cuda_runtime=registry["framework"]["cuda_runtime"],
            source_revision=registry["source_revision"],
        )

    def infer(self, features: list[list[float]], instance_id: int) -> BatchInferenceResult:
        torch = self._torch
        stream = self._streams[instance_id]
        model = self._models[instance_id]
        cpu_tensor = torch.tensor(features, dtype=torch.float32)
        h2d_start = torch.cuda.Event(enable_timing=True)
        h2d_end = torch.cuda.Event(enable_timing=True)
        inference_end = torch.cuda.Event(enable_timing=True)
        d2h_end = torch.cuda.Event(enable_timing=True)
        with torch.inference_mode(), torch.cuda.stream(stream):
            h2d_start.record(stream)
            inputs = cpu_tensor.to(self._device, non_blocking=False)
            h2d_end.record(stream)
            normalized = (inputs - self._mean) / self._scale
            probabilities = torch.sigmoid(model(normalized))
            inference_end.record(stream)
            cpu_probabilities = probabilities.to("cpu")
            d2h_end.record(stream)
        stream.synchronize()
        values = [float(value) for value in cpu_probabilities.tolist()]
        return BatchInferenceResult(
            probabilities=values,
            h2d_ms=float(h2d_start.elapsed_time(h2d_end)),
            inference_ms=float(h2d_end.elapsed_time(inference_end)),
            d2h_ms=float(inference_end.elapsed_time(d2h_end)),
            allocated_vram_bytes=int(torch.cuda.memory_allocated(self._device)),
            reserved_vram_bytes=int(torch.cuda.memory_reserved(self._device)),
            peak_vram_bytes=int(torch.cuda.max_memory_allocated(self._device)),
        )


class GpuBatchProbeExecutor:
    def __init__(
        self,
        config: GpuBatchExecutionConfig,
        *,
        backend: GpuBatchBackend | None = None,
    ):
        if not config.enabled:
            raise GpuBatchProbeError(
                "gpu_batch_runtime_disabled",
                "The S4 GPU batching runtime profile is disabled.",
                status_code=503,
            )
        self.config = config
        self.backend = backend or TorchGpuBatchBackend(config)
        self._queue: asyncio.Queue[_PendingRequest] = asyncio.Queue(maxsize=config.max_outstanding)
        self._permits = asyncio.Semaphore(config.max_outstanding)
        self._state_lock = asyncio.Lock()
        self._outstanding_bytes = 0
        self._worker_tasks: list[asyncio.Task[None]] = []
        self._started = False
        self._closed = False

    async def execute(self, request: GpuBatchProbeRequest) -> GpuBatchProbeResponse:
        self._validate_request_identity(request)
        self._assert_lease()
        await self._ensure_started()
        request_bytes = _canonical_request_bytes(request)
        if request_bytes > self.config.max_request_bytes:
            GPU_BATCH_ADMISSION_TOTAL.labels(outcome="rejected", reason="item_bytes").inc()
            raise GpuBatchProbeError(
                "gpu_batch_request_too_large",
                "Canonical S4 request exceeds the per-item byte bound.",
                status_code=413,
            )
        admitted_at = time.perf_counter()
        try:
            await asyncio.wait_for(
                self._permits.acquire(), timeout=self.config.admission_wait_seconds
            )
        except TimeoutError as exc:
            self._reject_capacity("count", exc)
        admission_wait = time.perf_counter() - admitted_at
        async with self._state_lock:
            if self._outstanding_bytes + request_bytes > self.config.max_outstanding_bytes:
                self._permits.release()
                self._reject_capacity("bytes")
            self._outstanding_bytes += request_bytes
            GPU_BATCH_QUEUE_BYTES.set(self._outstanding_bytes)
        loop = asyncio.get_running_loop()
        future: asyncio.Future[GpuBatchProbeResponse] = loop.create_future()
        pending = _PendingRequest(
            request=request,
            future=future,
            request_bytes=request_bytes,
            admitted_at=time.perf_counter(),
            admission_wait_seconds=admission_wait,
            parent_trace=current_trace_context(),
        )
        await self._queue.put(pending)
        GPU_BATCH_QUEUE_DEPTH.set(self._queue.qsize())
        GPU_BATCH_ADMISSION_TOTAL.labels(outcome="accepted", reason="within_bounds").inc()
        try:
            return await asyncio.wait_for(
                asyncio.shield(future), timeout=self.config.request_timeout_seconds
            )
        except TimeoutError as exc:
            raise GpuBatchProbeError(
                "gpu_batch_execution_timeout",
                "S4 GPU batch execution exceeded the request deadline.",
                status_code=504,
            ) from exc

    async def shutdown(self) -> None:
        if self._closed:
            return
        self._closed = True
        tasks = list(self._worker_tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        while not self._queue.empty():
            pending = self._queue.get_nowait()
            if not pending.future.done():
                pending.future.set_exception(
                    GpuBatchProbeError(
                        "gpu_batch_runtime_shutdown",
                        "The S4 GPU runtime shut down before execution.",
                        status_code=503,
                    )
                )
            await self._release_pending(pending)
            self._queue.task_done()
        GPU_BATCH_QUEUE_DEPTH.set(0)
        GPU_BATCH_QUEUE_BYTES.set(0)
        GPU_BATCH_IN_FLIGHT.set(0)

    async def _ensure_started(self) -> None:
        if self._started:
            return
        self._started = True
        self._worker_tasks = [
            asyncio.create_task(self._batch_worker(instance_id), name=f"s4-gpu-{instance_id}")
            for instance_id in range(self.config.instance_count)
        ]

    async def _batch_worker(self, instance_id: int) -> None:
        while True:
            first = await self._queue.get()
            batch = [first]
            deadline = time.perf_counter() + self.config.max_delay_ms / 1000
            while len(batch) < self.config.batch_size:
                remaining = deadline - time.perf_counter()
                if remaining <= 0:
                    break
                try:
                    batch.append(await asyncio.wait_for(self._queue.get(), timeout=remaining))
                except TimeoutError:
                    break
            GPU_BATCH_QUEUE_DEPTH.set(self._queue.qsize())
            await self._execute_batch(batch, instance_id)
            for _ in batch:
                self._queue.task_done()

    async def _execute_batch(self, batch: list[_PendingRequest], instance_id: int) -> None:
        started = time.perf_counter()
        queue_waits = [started - item.admitted_at for item in batch]
        GPU_BATCH_IN_FLIGHT.inc(len(batch))
        GPU_BATCH_INSTANCE_IN_FLIGHT.labels(instance_id=str(instance_id)).set(1)
        GPU_BATCH_FORMED_SIZE.observe(len(batch))
        for wait in queue_waits:
            GPU_BATCH_QUEUE_WAIT_SECONDS.observe(wait)
        try:
            self._assert_lease()
            with trace_span(
                "s4.gpu_batch.worker",
                parent=batch[0].parent_trace,
                kind="consumer",
                attributes={
                    "evm.stage": "gpu_batch_inference",
                    "evm.batch_size": len(batch),
                    "evm.configured_batch_size": self.config.batch_size,
                    "evm.max_delay_ms": self.config.max_delay_ms,
                    "evm.instance_id": instance_id,
                },
            ):
                result = await asyncio.to_thread(
                    self.backend.infer,
                    [item.request.features for item in batch],
                    instance_id,
                )
            if len(result.probabilities) != len(batch):
                raise GpuBatchProbeError(
                    "gpu_batch_result_cardinality_invalid",
                    "GPU result cardinality differs from the formed batch.",
                    status_code=503,
                )
            for stage, milliseconds in (
                ("h2d", result.h2d_ms),
                ("inference", result.inference_ms),
                ("d2h", result.d2h_ms),
            ):
                GPU_BATCH_STAGE_SECONDS.labels(stage=stage).observe(milliseconds / 1000)
            for kind, value in (
                ("allocated", result.allocated_vram_bytes),
                ("reserved", result.reserved_vram_bytes),
                ("peak", result.peak_vram_bytes),
            ):
                GPU_BATCH_VRAM_BYTES.labels(kind=kind).set(value)
            for index, pending in enumerate(batch):
                probability = result.probabilities[index]
                total_ms = (time.perf_counter() - pending.admitted_at) * 1000
                response = GpuBatchProbeResponse(
                    dataset_identity_sha256=pending.request.dataset_identity_sha256,
                    model_identity_sha256=pending.request.model_identity_sha256,
                    prediction=1 if probability >= 0.5 else 0,
                    positive_probability=probability,
                    timings=GpuBatchProbeTimings(
                        admission_wait_ms=pending.admission_wait_seconds * 1000,
                        queue_wait_ms=queue_waits[index] * 1000,
                        batch_wait_ms=queue_waits[index] * 1000,
                        h2d_ms=result.h2d_ms,
                        inference_ms=result.inference_ms,
                        d2h_ms=result.d2h_ms,
                        total_ms=total_ms,
                    ),
                    runtime=GpuBatchProbeRuntime(
                        instance_id=instance_id,
                        instance_count=self.config.instance_count,
                        configured_batch_size=self.config.batch_size,
                        formed_batch_size=len(batch),
                        max_delay_ms=self.config.max_delay_ms,
                        allocated_vram_bytes=result.allocated_vram_bytes,
                        reserved_vram_bytes=result.reserved_vram_bytes,
                        peak_vram_bytes=result.peak_vram_bytes,
                    ),
                )
                if not pending.future.done():
                    pending.future.set_result(response)
        except Exception as exc:
            for pending in batch:
                if not pending.future.done():
                    pending.future.set_exception(exc)
        finally:
            GPU_BATCH_IN_FLIGHT.dec(len(batch))
            GPU_BATCH_INSTANCE_IN_FLIGHT.labels(instance_id=str(instance_id)).set(0)
            for pending in batch:
                await self._release_pending(pending)

    async def _release_pending(self, pending: _PendingRequest) -> None:
        async with self._state_lock:
            self._outstanding_bytes = max(0, self._outstanding_bytes - pending.request_bytes)
            GPU_BATCH_QUEUE_BYTES.set(self._outstanding_bytes)
        self._permits.release()

    def _assert_lease(self) -> None:
        try:
            assert_scale_validation_gpu_lease_owner(
                run_id=self.config.lease_run_id,
                lease_id=self.config.lease_id,
                fencing_token=self.config.lease_fencing_token,
                purpose="scale_validation_inference",
            )
        except ScenarioWorkloadError as exc:
            raise GpuBatchProbeError(
                "gpu_batch_lease_identity_mismatch",
                "The exact S4 inference lease is not active.",
                status_code=409,
            ) from exc

    def _validate_request_identity(self, request: GpuBatchProbeRequest) -> None:
        descriptor = self.backend.descriptor
        if (
            request.dataset_identity_sha256 != descriptor.dataset_identity_sha256
            or request.model_identity_sha256 != descriptor.model_identity_sha256
        ):
            raise GpuBatchProbeError(
                "gpu_batch_request_identity_mismatch",
                "Request identity differs from the loaded S4 artifact.",
                status_code=409,
            )

    def _reject_capacity(self, reason: str, cause: Exception | None = None) -> None:
        GPU_BATCH_ADMISSION_TOTAL.labels(outcome="rejected", reason=reason).inc()
        error = GpuBatchProbeError(
            "gpu_batch_executor_saturated",
            "S4 GPU batching capacity is saturated; retry later.",
            status_code=429,
            headers={"Retry-After": str(self.config.retry_after_seconds)},
        )
        if cause is not None:
            raise error from cause
        raise error


_EXECUTOR_LOCK = asyncio.Lock()
_EXECUTOR: GpuBatchProbeExecutor | None = None


async def execute_gpu_batch_probe(
    request: GpuBatchProbeRequest,
) -> GpuBatchProbeResponse:
    return await _gpu_batch_executor().execute(request)


def load_gpu_batch_probe_descriptor() -> GpuBatchProbeDescriptor:
    return _gpu_batch_executor().backend.descriptor


async def shutdown_gpu_batch_probe_executor() -> None:
    global _EXECUTOR
    async with _EXECUTOR_LOCK:
        executor = _EXECUTOR
        _EXECUTOR = None
    if executor is not None:
        await executor.shutdown()


def _gpu_batch_executor() -> GpuBatchProbeExecutor:
    global _EXECUTOR
    config = GpuBatchExecutionConfig.from_environment()
    if _EXECUTOR is not None and _EXECUTOR.config != config:
        raise GpuBatchProbeError(
            "gpu_batch_runtime_config_changed",
            "S4 GPU batching configuration is immutable for one process lifetime.",
            status_code=503,
        )
    if _EXECUTOR is None:
        _EXECUTOR = GpuBatchProbeExecutor(config)
    return _EXECUTOR


def _load_registry(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise GpuBatchProbeError(
            "gpu_batch_registry_invalid",
            "The S4 model registry cannot be read.",
            status_code=503,
        ) from exc
    required = {
        "schema_version",
        "dataset_version",
        "dataset_identity_sha256",
        "split_manifest_sha256",
        "model_identity_sha256",
        "artifact_uri",
        "artifact_sha256",
        "architecture",
        "preprocessing",
        "framework",
        "source_revision",
    }
    if (
        not isinstance(payload, dict)
        or required - payload.keys()
        or payload.get("schema_version") != "evm.s4_gpu_batch_registry.v1"
        or payload.get("architecture") != "28-64-32-1-relu-fp32"
        or not all(
            _is_sha256(payload.get(key))
            for key in (
                "dataset_identity_sha256",
                "split_manifest_sha256",
                "model_identity_sha256",
                "artifact_sha256",
            )
        )
        or len(str(payload.get("source_revision") or "")) != 40
    ):
        raise GpuBatchProbeError(
            "gpu_batch_registry_contract_invalid",
            "The S4 model registry contract is incomplete or invalid.",
            status_code=503,
        )
    preprocessing = payload.get("preprocessing")
    if not isinstance(preprocessing, dict):
        raise GpuBatchProbeError(
            "gpu_batch_preprocessing_invalid",
            "The S4 preprocessing identity is missing.",
            status_code=503,
        )
    for key in ("mean", "scale"):
        values = preprocessing.get(key)
        if (
            not isinstance(values, list)
            or len(values) != 28
            or any(
                not isinstance(value, (int, float)) or not math.isfinite(value) for value in values
            )
        ):
            raise GpuBatchProbeError(
                "gpu_batch_preprocessing_invalid",
                "The S4 preprocessing vector is invalid.",
                status_code=503,
            )
    if any(float(value) <= 0 for value in preprocessing["scale"]):
        raise GpuBatchProbeError(
            "gpu_batch_preprocessing_invalid",
            "The S4 preprocessing scale must be positive.",
            status_code=503,
        )
    return payload


def _resolve_artifact(registry_path: Path, value: str) -> Path:
    candidate = Path(value)
    return candidate if candidate.is_absolute() else registry_path.parent / candidate


def _canonical_request_bytes(request: GpuBatchProbeRequest) -> int:
    return len(
        json.dumps(
            request.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    )


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _is_sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _environment_bool(name: str, default: bool) -> bool:
    value = os.getenv(name, str(default)).strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    raise GpuBatchProbeError(
        "gpu_batch_config_invalid", f"{name} must be boolean.", status_code=503
    )


def _environment_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)).strip())
    except ValueError as exc:
        raise GpuBatchProbeError(
            "gpu_batch_config_invalid", f"{name} must be an integer.", status_code=503
        ) from exc
    if not minimum <= value <= maximum:
        raise GpuBatchProbeError(
            "gpu_batch_config_invalid",
            f"{name} must be between {minimum} and {maximum}.",
            status_code=503,
        )
    return value


def _environment_float(name: str, default: float, minimum: float, maximum: float) -> float:
    try:
        value = float(os.getenv(name, str(default)).strip())
    except ValueError as exc:
        raise GpuBatchProbeError(
            "gpu_batch_config_invalid", f"{name} must be numeric.", status_code=503
        ) from exc
    if not minimum <= value <= maximum:
        raise GpuBatchProbeError(
            "gpu_batch_config_invalid",
            f"{name} must be between {minimum} and {maximum}.",
            status_code=503,
        )
    return value
