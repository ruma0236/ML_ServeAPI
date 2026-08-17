from __future__ import annotations

import asyncio
import json
import os
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from dataclasses import dataclass

from prometheus_client import Counter, Gauge, Histogram

from evm.control_panel.scenario_workloads import (
    CapacityProbeRequest,
    CapacityProbeResponse,
    CapacityProbeRuntime,
)
from evm.model_runtime.capacity_probe import CapacityProbeError, run_capacity_probe
from evm.observability.otel import trace_span
from evm.observability.trace_context import W3CTraceContext, current_trace_context


CAPACITY_EXECUTOR_ADMISSION_TOTAL = Counter(
    "evm_s3_capacity_executor_admission_total",
    "S3 process-local capacity admission outcomes by bounded reason.",
    ("outcome", "reason"),
)
CAPACITY_EXECUTOR_RETRY_AFTER_SECONDS = Histogram(
    "evm_s3_capacity_executor_retry_after_seconds",
    "Retry-After values returned by bounded S3 capacity admission.",
    buckets=(1, 2, 3, 5, 10),
)
CAPACITY_EXECUTOR_ADMISSION_WAIT_SECONDS = Histogram(
    "evm_s3_capacity_executor_admission_wait_seconds",
    "Time waiting for a process-local S3 capacity permit.",
    buckets=(0.00001, 0.00005, 0.0001, 0.0005, 0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5),
)
CAPACITY_EXECUTOR_QUEUE_WAIT_SECONDS = Histogram(
    "evm_s3_capacity_executor_queue_wait_seconds",
    "Time accepted S3 capacity work waits for a CPU worker.",
    buckets=(0.00001, 0.00005, 0.0001, 0.0005, 0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2),
)
CAPACITY_EXECUTOR_QUEUE_DEPTH = Gauge(
    "evm_s3_capacity_executor_queue_depth",
    "Accepted S3 capacity requests waiting for a process-local CPU worker.",
)
CAPACITY_EXECUTOR_QUEUE_BYTES = Gauge(
    "evm_s3_capacity_executor_queue_bytes",
    "Canonical bytes held by the process-local S3 worker queue.",
)
CAPACITY_EXECUTOR_IN_FLIGHT = Gauge(
    "evm_s3_capacity_executor_in_flight",
    "S3 capacity requests currently executing in the process-local worker pool.",
)
CAPACITY_EXECUTOR_IN_FLIGHT_BYTES = Gauge(
    "evm_s3_capacity_executor_in_flight_bytes",
    "Canonical request bytes currently executing in the S3 worker pool.",
)
CAPACITY_EXECUTOR_OUTSTANDING = Gauge(
    "evm_s3_capacity_executor_outstanding",
    "Accepted queued and executing S3 capacity requests.",
)
CAPACITY_EXECUTOR_OUTSTANDING_BYTES = Gauge(
    "evm_s3_capacity_executor_outstanding_bytes",
    "Canonical bytes held by accepted queued and executing S3 capacity requests.",
)
CAPACITY_EXECUTOR_WORKER_TASKS = Counter(
    "evm_s3_capacity_executor_worker_tasks_total",
    "S3 capacity tasks completed by bounded worker slot and outcome.",
    ("worker_slot", "outcome"),
)
CAPACITY_EXECUTOR_INFO = Gauge(
    "evm_s3_capacity_executor_info",
    "Frozen process-local S3 capacity executor configuration.",
    ("cpu_workers", "max_outstanding"),
)


@dataclass(frozen=True)
class CapacityExecutionConfig:
    cpu_workers: int
    max_outstanding: int
    max_outstanding_bytes: int
    max_request_bytes: int
    admission_wait_seconds: float
    request_timeout_seconds: float
    retry_after_seconds: int
    replica_id: str

    @classmethod
    def from_environment(cls) -> "CapacityExecutionConfig":
        config = cls(
            cpu_workers=_environment_int("EVM_S3_CAPACITY_CPU_WORKERS", 1, 1, 64),
            max_outstanding=_environment_int(
                "EVM_S3_CAPACITY_MAX_OUTSTANDING", 128, 1, 4096
            ),
            max_outstanding_bytes=_environment_int(
                "EVM_S3_CAPACITY_MAX_OUTSTANDING_BYTES", 1048576, 1024, 268435456
            ),
            max_request_bytes=_environment_int(
                "EVM_S3_CAPACITY_MAX_REQUEST_BYTES", 8192, 256, 1048576
            ),
            admission_wait_seconds=_environment_float(
                "EVM_S3_CAPACITY_ADMISSION_WAIT_SECONDS", 0.05, 0, 30
            ),
            request_timeout_seconds=_environment_float(
                "EVM_S3_CAPACITY_REQUEST_TIMEOUT_SECONDS", 5.0, 0.1, 300
            ),
            retry_after_seconds=_environment_int(
                "EVM_S3_CAPACITY_RETRY_AFTER_SECONDS", 1, 1, 3600
            ),
            replica_id=_replica_id(),
        )
        if config.max_outstanding < config.cpu_workers:
            raise CapacityProbeError(
                "capacity_executor_config_invalid",
                "S3 max outstanding requests cannot be lower than CPU workers.",
                status_code=503,
            )
        if config.max_outstanding_bytes < config.max_request_bytes:
            raise CapacityProbeError(
                "capacity_executor_config_invalid",
                "S3 outstanding byte capacity cannot be lower than one request.",
                status_code=503,
            )
        return config


@dataclass(frozen=True)
class _WorkerResult:
    response: CapacityProbeResponse
    worker_slot: int
    queue_wait_seconds: float


@dataclass(frozen=True)
class _AdmittedWork:
    future: Future[_WorkerResult]
    request_bytes: int
    admission_wait_seconds: float


class CapacityProbeExecutor:
    def __init__(self, config: CapacityExecutionConfig):
        self.config = config
        self._permits = threading.BoundedSemaphore(config.max_outstanding)
        self._pool = ThreadPoolExecutor(
            max_workers=config.cpu_workers,
            thread_name_prefix="s3-capacity",
        )
        self._lock = threading.Lock()
        self._queued_count = 0
        self._queued_bytes = 0
        self._in_flight_count = 0
        self._in_flight_bytes = 0
        self._thread_slots: dict[int, int] = {}
        CAPACITY_EXECUTOR_INFO.labels(
            cpu_workers=str(config.cpu_workers),
            max_outstanding=str(config.max_outstanding),
        ).set(1)
        self._publish_state()

    def execute(self, request: CapacityProbeRequest) -> CapacityProbeResponse:
        admitted = self._admit(request)
        try:
            worker_result = admitted.future.result(
                timeout=self.config.request_timeout_seconds
            )
        except FutureTimeoutError as exc:
            raise self._timeout_error() from exc
        return self._response(admitted, worker_result)

    async def execute_async(
        self, request: CapacityProbeRequest
    ) -> CapacityProbeResponse:
        admitted = await self._admit_async(request)
        try:
            worker_result = await asyncio.wait_for(
                asyncio.shield(asyncio.wrap_future(admitted.future)),
                timeout=self.config.request_timeout_seconds,
            )
        except TimeoutError as exc:
            raise self._timeout_error() from exc
        return self._response(admitted, worker_result)

    def shutdown(self) -> None:
        CAPACITY_EXECUTOR_INFO.labels(
            cpu_workers=str(self.config.cpu_workers),
            max_outstanding=str(self.config.max_outstanding),
        ).set(0)
        self._pool.shutdown(wait=True, cancel_futures=False)

    def _admit(self, request: CapacityProbeRequest) -> _AdmittedWork:
        request_bytes = _canonical_request_bytes(request)
        self._assert_item_size(request_bytes)

        admission_started = time.perf_counter()
        with trace_span(
            "s3.capacity.admission",
            kind="producer",
            attributes={
                "evm.stage": "admission",
                "evm.probe_family": request.probe_family,
                "evm.request_bytes": request_bytes,
            },
        ) as admission_span:
            acquired = self._permits.acquire(
                timeout=self.config.admission_wait_seconds
            )
            admission_wait = time.perf_counter() - admission_started
            CAPACITY_EXECUTOR_ADMISSION_WAIT_SECONDS.observe(admission_wait)
            admission_span.set_attribute("evm.admission_wait_seconds", admission_wait)
            if not acquired:
                self._reject_capacity("count")
            self._reserve_bytes(request_bytes)

        return self._submit(request, request_bytes, admission_wait)

    async def _admit_async(self, request: CapacityProbeRequest) -> _AdmittedWork:
        request_bytes = _canonical_request_bytes(request)
        self._assert_item_size(request_bytes)
        admission_started = time.perf_counter()
        deadline = admission_started + self.config.admission_wait_seconds
        with trace_span(
            "s3.capacity.admission",
            kind="producer",
            attributes={
                "evm.stage": "admission",
                "evm.probe_family": request.probe_family,
                "evm.request_bytes": request_bytes,
            },
        ) as admission_span:
            acquired = self._permits.acquire(blocking=False)
            while not acquired and time.perf_counter() < deadline:
                await asyncio.sleep(
                    min(0.001, max(0.0, deadline - time.perf_counter()))
                )
                acquired = self._permits.acquire(blocking=False)
            admission_wait = time.perf_counter() - admission_started
            CAPACITY_EXECUTOR_ADMISSION_WAIT_SECONDS.observe(admission_wait)
            admission_span.set_attribute("evm.admission_wait_seconds", admission_wait)
            if not acquired:
                self._reject_capacity("count")
            self._reserve_bytes(request_bytes)

        return self._submit(request, request_bytes, admission_wait)

    def _assert_item_size(self, request_bytes: int) -> None:
        if request_bytes <= self.config.max_request_bytes:
            return
        CAPACITY_EXECUTOR_ADMISSION_TOTAL.labels(
            outcome="rejected", reason="item_bytes"
        ).inc()
        raise CapacityProbeError(
            "capacity_request_too_large",
            "Canonical S3 capacity request exceeds the per-item byte bound.",
            status_code=413,
        )

    def _reserve_bytes(self, request_bytes: int) -> None:
        with self._lock:
            outstanding_bytes = self._queued_bytes + self._in_flight_bytes
            if outstanding_bytes + request_bytes > self.config.max_outstanding_bytes:
                self._permits.release()
                self._reject_capacity("bytes")
            self._queued_count += 1
            self._queued_bytes += request_bytes
            self._publish_state_locked()

    def _submit(
        self,
        request: CapacityProbeRequest,
        request_bytes: int,
        admission_wait: float,
    ) -> _AdmittedWork:
        accepted_at = time.perf_counter()
        parent = current_trace_context()
        future = self._pool.submit(
            self._execute_worker,
            request,
            request_bytes,
            accepted_at,
            parent,
        )
        CAPACITY_EXECUTOR_ADMISSION_TOTAL.labels(
            outcome="accepted", reason="within_bounds"
        ).inc()
        return _AdmittedWork(
            future=future,
            request_bytes=request_bytes,
            admission_wait_seconds=admission_wait,
        )

    def _response(
        self,
        admitted: _AdmittedWork,
        worker_result: _WorkerResult,
    ) -> CapacityProbeResponse:
        timings = worker_result.response.timings.model_copy(
            update={
                "admission_wait_ms": admitted.admission_wait_seconds * 1000,
                "queue_wait_ms": worker_result.queue_wait_seconds * 1000,
                "total_ms": (
                    admitted.admission_wait_seconds
                    + worker_result.queue_wait_seconds
                    + worker_result.response.timings.compute_ms / 1000
                )
                * 1000,
            }
        )
        return worker_result.response.model_copy(
            update={
                "timings": timings,
                "runtime": CapacityProbeRuntime(
                    api_replica_id=self.config.replica_id,
                    cpu_worker_count=self.config.cpu_workers,
                    worker_slot=worker_result.worker_slot,
                    canonical_request_bytes=admitted.request_bytes,
                ),
            }
        )

    def _timeout_error(self) -> CapacityProbeError:
        CAPACITY_EXECUTOR_ADMISSION_TOTAL.labels(
            outcome="timeout", reason="worker_deadline"
        ).inc()
        return CapacityProbeError(
            "capacity_execution_timeout",
            "S3 capacity execution exceeded the bounded request timeout.",
            status_code=504,
        )

    def _execute_worker(
        self,
        request: CapacityProbeRequest,
        request_bytes: int,
        accepted_at: float,
        parent: W3CTraceContext | None,
    ) -> _WorkerResult:
        started = time.perf_counter()
        queue_wait = started - accepted_at
        worker_slot = self._worker_slot()
        with self._lock:
            self._queued_count -= 1
            self._queued_bytes -= request_bytes
            self._in_flight_count += 1
            self._in_flight_bytes += request_bytes
            self._publish_state_locked()
        CAPACITY_EXECUTOR_QUEUE_WAIT_SECONDS.observe(queue_wait)
        outcome = "error"
        try:
            with trace_span(
                "s3.capacity.worker",
                parent=parent,
                kind="consumer",
                attributes={
                    "evm.stage": "worker",
                    "evm.probe_family": request.probe_family,
                    "evm.api_replica_id": self.config.replica_id,
                    "evm.worker_slot": worker_slot,
                    "evm.queue_wait_seconds": queue_wait,
                    "evm.request_bytes": request_bytes,
                },
            ):
                response = run_capacity_probe(request)
            outcome = "ok"
            return _WorkerResult(
                response=response,
                worker_slot=worker_slot,
                queue_wait_seconds=queue_wait,
            )
        except CapacityProbeError:
            outcome = "rejected"
            raise
        finally:
            CAPACITY_EXECUTOR_WORKER_TASKS.labels(
                worker_slot=str(worker_slot), outcome=outcome
            ).inc()
            with self._lock:
                self._in_flight_count -= 1
                self._in_flight_bytes -= request_bytes
                self._publish_state_locked()
            self._permits.release()

    def _worker_slot(self) -> int:
        identity = threading.get_ident()
        with self._lock:
            slot = self._thread_slots.get(identity)
            if slot is None:
                slot = len(self._thread_slots)
                if slot >= self.config.cpu_workers:
                    raise RuntimeError("capacity worker slot exceeds configured pool")
                self._thread_slots[identity] = slot
            return slot

    def _reject_capacity(self, reason: str) -> None:
        CAPACITY_EXECUTOR_ADMISSION_TOTAL.labels(
            outcome="rejected", reason=reason
        ).inc()
        CAPACITY_EXECUTOR_RETRY_AFTER_SECONDS.observe(
            self.config.retry_after_seconds
        )
        raise CapacityProbeError(
            "capacity_executor_saturated",
            "S3 process-local capacity is saturated; retry later.",
            status_code=429,
            headers={"Retry-After": str(self.config.retry_after_seconds)},
        )

    def _publish_state(self) -> None:
        with self._lock:
            self._publish_state_locked()

    def _publish_state_locked(self) -> None:
        CAPACITY_EXECUTOR_QUEUE_DEPTH.set(self._queued_count)
        CAPACITY_EXECUTOR_QUEUE_BYTES.set(self._queued_bytes)
        CAPACITY_EXECUTOR_IN_FLIGHT.set(self._in_flight_count)
        CAPACITY_EXECUTOR_IN_FLIGHT_BYTES.set(self._in_flight_bytes)
        CAPACITY_EXECUTOR_OUTSTANDING.set(
            self._queued_count + self._in_flight_count
        )
        CAPACITY_EXECUTOR_OUTSTANDING_BYTES.set(
            self._queued_bytes + self._in_flight_bytes
        )


_EXECUTOR_LOCK = threading.Lock()
_EXECUTOR: CapacityProbeExecutor | None = None


def execute_capacity_probe(request: CapacityProbeRequest) -> CapacityProbeResponse:
    return _capacity_executor().execute(request)


async def execute_capacity_probe_async(
    request: CapacityProbeRequest,
) -> CapacityProbeResponse:
    return await _capacity_executor().execute_async(request)


def shutdown_capacity_probe_executor() -> None:
    global _EXECUTOR
    with _EXECUTOR_LOCK:
        executor = _EXECUTOR
        _EXECUTOR = None
    if executor is not None:
        executor.shutdown()


def _capacity_executor() -> CapacityProbeExecutor:
    global _EXECUTOR
    config = CapacityExecutionConfig.from_environment()
    with _EXECUTOR_LOCK:
        if _EXECUTOR is not None and _EXECUTOR.config != config:
            previous = _EXECUTOR
            _EXECUTOR = None
            previous.shutdown()
        if _EXECUTOR is None:
            _EXECUTOR = CapacityProbeExecutor(config)
        return _EXECUTOR


def _canonical_request_bytes(request: CapacityProbeRequest) -> int:
    return len(
        json.dumps(
            request.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    )


def _environment_int(name: str, default: int, minimum: int, maximum: int) -> int:
    raw = os.getenv(name, str(default)).strip()
    try:
        value = int(raw)
    except ValueError as exc:
        raise CapacityProbeError(
            "capacity_executor_config_invalid",
            f"{name} must be an integer.",
            status_code=503,
        ) from exc
    if not minimum <= value <= maximum:
        raise CapacityProbeError(
            "capacity_executor_config_invalid",
            f"{name} must be between {minimum} and {maximum}.",
            status_code=503,
        )
    return value


def _environment_float(
    name: str,
    default: float,
    minimum: float,
    maximum: float,
) -> float:
    raw = os.getenv(name, str(default)).strip()
    try:
        value = float(raw)
    except ValueError as exc:
        raise CapacityProbeError(
            "capacity_executor_config_invalid",
            f"{name} must be numeric.",
            status_code=503,
        ) from exc
    if not minimum <= value <= maximum:
        raise CapacityProbeError(
            "capacity_executor_config_invalid",
            f"{name} must be between {minimum} and {maximum}.",
            status_code=503,
        )
    return value


def _replica_id() -> str:
    value = os.getenv("EVM_S3_CAPACITY_REPLICA_ID", "replica-0").strip()
    if not value or len(value) > 64 or any(
        character not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_"
        for character in value
    ):
        raise CapacityProbeError(
            "capacity_executor_config_invalid",
            "EVM_S3_CAPACITY_REPLICA_ID must be a bounded safe identifier.",
            status_code=503,
        )
    return value
