from __future__ import annotations

import hashlib
import json
import math
import os
import platform
import random
import statistics
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from threading import BoundedSemaphore
from typing import Any, Callable, Iterable

import requests

from evm.control_panel.scenario_workload_production import ScenarioProductionIntent
from evm.core.mlflow_client import MlflowRestClient
from evm.model_runtime.common import nvidia_smi_snapshot
from evm.model_runtime.scenario_workload_production import verify_production_inference
from evm.observability.otel import configure_tracing, runtime_service_version, trace_span
from evm.observability.trace_context import W3CTraceContext
from evm.scale_validation.contracts import (
    BENCHMARK_SCHEMA_VERSION,
    BenchmarkClosure,
    BenchmarkControlRun,
    BenchmarkEnvironment,
    BenchmarkEvidence,
    BenchmarkIdentity,
    EvidenceArtifact,
    InferenceMeasurementWindow,
    LoadProfile,
    MetricObservation,
    TracePropagationEvidence,
)
from evm.scale_validation.evidence import public_file_sha256, write_public_json


REQUIRED_TRACE_STAGES = ("api", "queue", "worker", "spark", "mlflow", "serving")
REQUIRED_TARGET_JOBS = ("evm-api", "evm-lifecycle-serving", "evm-otel-collector")


class S0RuntimeError(RuntimeError):
    pass


@dataclass(frozen=True)
class S0RuntimeConfig:
    api_url: str = "http://127.0.0.1:8000"
    mlflow_url: str = "http://127.0.0.1:5000"
    prometheus_url: str = "http://127.0.0.1:9090"
    trace_file: Path = Path(
        "F:/EnterpriseMLOps_Data/enterprise-vision-mlops/"
        "artifacts/scale_validation/otel/traces.json"
    )
    private_evidence_root: Path = Path(
        "F:/EnterpriseMLOps_Data/enterprise-vision-mlops/"
        "artifacts/scale_validation/s0"
    )
    public_evidence_root: Path = Path("docs/status/evidence")
    profile_id: str = "portfolio-live-b0-production-ui-preflight"
    profile_version: int = 1
    repetitions: int = 3
    serving_requests_per_run: int = 3
    lifecycle_timeout_seconds: float = 900.0
    trace_timeout_seconds: float = 45.0
    poll_interval_seconds: float = 2.0
    http_timeout_seconds: float = 30.0
    target_requests_per_second: float = 0.05
    load_duration_seconds: float = 60.0
    seed: int = 20260815


@dataclass(frozen=True)
class HttpObservation:
    payload: Any
    latency_seconds: float
    permit_wait_seconds: float


@dataclass(frozen=True)
class PacedInferenceResult:
    latencies_seconds: tuple[float, ...]
    request_start_offsets_seconds: tuple[float, ...]
    request_start_lag_seconds: tuple[float, ...]
    observed_elapsed_seconds: float
    sample_ids: tuple[str, ...]
    sample_selectors: tuple[int, ...]


def fixed_window_request_count(*, target_rps: float, duration_seconds: float) -> int:
    if target_rps <= 0 or duration_seconds <= 0:
        raise S0RuntimeError("s0_load_profile_non_positive")
    expected = target_rps * duration_seconds
    request_count = int(round(expected))
    if request_count < 1 or not math.isclose(
        expected, request_count, rel_tol=0, abs_tol=1e-9
    ):
        raise S0RuntimeError("s0_load_profile_request_count_non_integral")
    return request_count


def deterministic_sample_selectors(*, seed: int, request_count: int) -> tuple[int, ...]:
    if seed < 0 or request_count < 1:
        raise S0RuntimeError("s0_seed_contract_invalid")
    generator = random.Random(seed)
    return tuple(generator.randrange(0, 2**31) for _ in range(request_count))


def execute_fixed_window_requests(
    *,
    target_rps: float,
    duration_seconds: float,
    request_count: int,
    seed: int,
    request: Callable[[int], str],
    monotonic: Callable[[], float] = time.perf_counter,
    sleep: Callable[[float], None] = time.sleep,
    max_start_lag_seconds: float = 2.0,
    max_window_overrun_seconds: float = 2.0,
) -> PacedInferenceResult:
    expected_count = fixed_window_request_count(
        target_rps=target_rps,
        duration_seconds=duration_seconds,
    )
    if request_count != expected_count:
        raise S0RuntimeError(
            f"s0_load_profile_request_count_mismatch:{request_count}:{expected_count}"
        )
    selectors = deterministic_sample_selectors(seed=seed, request_count=request_count)
    interval_seconds = 1.0 / target_rps
    window_started = monotonic()
    latencies: list[float] = []
    offsets: list[float] = []
    lags: list[float] = []
    sample_ids: list[str] = []

    for index, selector in enumerate(selectors):
        scheduled_offset = index * interval_seconds
        delay = window_started + scheduled_offset - monotonic()
        if delay > 0:
            sleep(delay)
        request_started = monotonic()
        observed_offset = request_started - window_started
        start_lag = max(0.0, observed_offset - scheduled_offset)
        if start_lag > max_start_lag_seconds:
            raise S0RuntimeError(f"s0_request_schedule_lag:{index}:{start_lag}")
        sample_ids.append(request(selector))
        request_finished = monotonic()
        if request_finished - window_started > duration_seconds:
            raise S0RuntimeError(f"s0_request_completed_outside_window:{index}")
        latencies.append(request_finished - request_started)
        offsets.append(observed_offset)
        lags.append(start_lag)

    remaining = window_started + duration_seconds - monotonic()
    if remaining > 0:
        sleep(remaining)
    elapsed = monotonic() - window_started
    if elapsed < duration_seconds:
        raise S0RuntimeError("s0_measurement_window_ended_early")
    if elapsed > duration_seconds + max_window_overrun_seconds:
        raise S0RuntimeError(f"s0_measurement_window_overrun:{elapsed}")
    return PacedInferenceResult(
        latencies_seconds=tuple(latencies),
        request_start_offsets_seconds=tuple(offsets),
        request_start_lag_seconds=tuple(lags),
        observed_elapsed_seconds=elapsed,
        sample_ids=tuple(sample_ids),
        sample_selectors=selectors,
    )


class BoundedHttpClient:
    def __init__(self, *, capacity: int = 1, timeout_seconds: float = 30.0) -> None:
        self._permit = BoundedSemaphore(capacity)
        self._session = requests.Session()
        self.timeout_seconds = timeout_seconds
        self.permit_wait_seconds: list[float] = []

    def json(
        self,
        method: str,
        url: str,
        *,
        trace_context: W3CTraceContext,
        payload: dict[str, Any] | None = None,
        timeout_seconds: float | None = None,
    ) -> HttpObservation:
        wait_started = time.perf_counter()
        acquired = self._permit.acquire(timeout=timeout_seconds or self.timeout_seconds)
        permit_wait = time.perf_counter() - wait_started
        self.permit_wait_seconds.append(permit_wait)
        if not acquired:
            raise S0RuntimeError("s0_http_permit_timeout")
        started = time.perf_counter()
        try:
            response = self._session.request(
                method,
                url,
                json=payload,
                headers=trace_context.headers(),
                timeout=timeout_seconds or self.timeout_seconds,
            )
        finally:
            self._permit.release()
        elapsed = time.perf_counter() - started
        try:
            body = response.json()
        except ValueError as exc:
            raise S0RuntimeError(
                f"s0_http_non_json:{method}:{url}:{response.status_code}"
            ) from exc
        if not response.ok:
            raise S0RuntimeError(
                f"s0_http_failed:{method}:{url}:{response.status_code}:{body}"
            )
        return HttpObservation(body, elapsed, permit_wait)

    def close(self) -> None:
        self._session.close()


def utc_now() -> datetime:
    return datetime.now(UTC)


def canonical_sha256(payload: Any) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def percentile(values: Iterable[float], quantile: float) -> float:
    ordered = sorted(float(value) for value in values)
    if not ordered:
        raise S0RuntimeError("s0_metric_samples_missing")
    index = min(len(ordered) - 1, max(0, math.ceil(len(ordered) * quantile) - 1))
    return ordered[index]


def summary_statistics(values: Iterable[float]) -> dict[str, float]:
    samples = [float(value) for value in values]
    if not samples:
        raise S0RuntimeError("s0_metric_samples_missing")
    return {
        "min": min(samples),
        "mean": statistics.fmean(samples),
        "max": max(samples),
    }


def request_latency_statistics(values: Iterable[float]) -> dict[str, float]:
    samples = [float(value) for value in values]
    return {
        **summary_statistics(samples),
        "p50": percentile(samples, 0.50),
        "p95": percentile(samples, 0.95),
        "p99": percentile(samples, 0.99),
    }


def coefficient_of_variation(values: Iterable[float]) -> float:
    samples = [float(value) for value in values]
    if not samples:
        raise S0RuntimeError("s0_variance_samples_missing")
    average = statistics.fmean(samples)
    if average == 0:
        return 0.0
    return statistics.pstdev(samples) / average


def total_ram_gib() -> float:
    if platform.system() == "Windows":
        import ctypes

        class MemoryStatus(ctypes.Structure):
            _fields_ = [
                ("length", ctypes.c_ulong),
                ("memory_load", ctypes.c_ulong),
                ("total_physical", ctypes.c_ulonglong),
                ("available_physical", ctypes.c_ulonglong),
                ("total_page_file", ctypes.c_ulonglong),
                ("available_page_file", ctypes.c_ulonglong),
                ("total_virtual", ctypes.c_ulonglong),
                ("available_virtual", ctypes.c_ulonglong),
                ("available_extended_virtual", ctypes.c_ulonglong),
            ]

        status = MemoryStatus()
        status.length = ctypes.sizeof(MemoryStatus)
        if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            raise S0RuntimeError("s0_host_memory_inventory_failed")
        return status.total_physical / (1024**3)
    page_size = os.sysconf("SC_PAGE_SIZE")
    page_count = os.sysconf("SC_PHYS_PAGES")
    return float(page_size * page_count) / (1024**3)


def _otlp_value(payload: dict[str, Any]) -> Any:
    for key in ("stringValue", "boolValue", "intValue", "doubleValue", "bytesValue"):
        if key in payload:
            return payload[key]
    return None


def read_trace_spans(path: Path, trace_id: str) -> list[dict[str, Any]]:
    spans: list[dict[str, Any]] = []
    if not path.is_file():
        return spans
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if trace_id not in line:
                continue
            try:
                batch = json.loads(line)
            except json.JSONDecodeError:
                continue
            for resource_spans in batch.get("resourceSpans", []):
                resource = {
                    item.get("key"): _otlp_value(item.get("value", {}))
                    for item in resource_spans.get("resource", {}).get("attributes", [])
                }
                for scope_spans in resource_spans.get("scopeSpans", []):
                    for span in scope_spans.get("spans", []):
                        if span.get("traceId") != trace_id:
                            continue
                        attributes = {
                            item.get("key"): _otlp_value(item.get("value", {}))
                            for item in span.get("attributes", [])
                        }
                        spans.append(
                            {
                                "name": span.get("name"),
                                "span_id": span.get("spanId"),
                                "parent_span_id": span.get("parentSpanId"),
                                "stage": attributes.get("evm.stage"),
                                "service_name": resource.get("service.name"),
                                "service_version": resource.get("service.version"),
                                "attributes": attributes,
                            }
                        )
    return spans


def wait_for_trace_stages(
    path: Path,
    trace_id: str,
    *,
    required_stages: Iterable[str] = REQUIRED_TRACE_STAGES,
    timeout_seconds: float,
    poll_interval_seconds: float,
) -> list[dict[str, Any]]:
    required = set(required_stages)
    deadline = time.monotonic() + timeout_seconds
    last: list[dict[str, Any]] = []
    while time.monotonic() < deadline:
        last = read_trace_spans(path, trace_id)
        observed = {str(item["stage"]) for item in last if item.get("stage")}
        if required.issubset(observed):
            return last
        time.sleep(poll_interval_seconds)
    observed = {str(item["stage"]) for item in last if item.get("stage")}
    raise S0RuntimeError(f"s0_trace_stages_missing:{sorted(required - observed)}")


def _prometheus_value(prometheus_url: str, query: str) -> float:
    response = requests.get(
        f"{prometheus_url.rstrip('/')}/api/v1/query",
        params={"query": query},
        timeout=15,
    )
    response.raise_for_status()
    results = response.json().get("data", {}).get("result", [])
    if len(results) != 1:
        raise S0RuntimeError(f"s0_prometheus_cardinality:{query}:{len(results)}")
    return float(results[0]["value"][1])


def assert_required_targets_healthy(prometheus_url: str) -> list[dict[str, Any]]:
    response = requests.get(
        f"{prometheus_url.rstrip('/')}/api/v1/targets",
        timeout=15,
    )
    response.raise_for_status()
    active = response.json().get("data", {}).get("activeTargets", [])
    selected: list[dict[str, Any]] = []
    for job in REQUIRED_TARGET_JOBS:
        matches = [target for target in active if target.get("labels", {}).get("job") == job]
        if len(matches) != 1:
            raise S0RuntimeError(f"s0_target_cardinality:{job}:{len(matches)}")
        target = matches[0]
        if target.get("health") != "up" or target.get("lastError"):
            raise S0RuntimeError(f"s0_target_unhealthy:{job}")
        selected.append(
            {
                "job": job,
                "health": target.get("health"),
                "last_error": target.get("lastError") or "",
            }
        )
    return selected


def _stage(run: dict[str, Any], stage_id: str) -> dict[str, Any]:
    matches = [stage for stage in run.get("stages", []) if stage.get("stage_id") == stage_id]
    if len(matches) != 1:
        raise S0RuntimeError(f"s0_stage_cardinality:{stage_id}:{len(matches)}")
    return matches[0]


def _metric(metric: str, unit: str, values: list[float], query: str) -> MetricObservation:
    return MetricObservation(
        metric=metric,
        unit=unit,
        sample_count=len(values),
        statistics=summary_statistics(values),
        query=query,
    )


def _write_json(path: Path, payload: Any) -> None:
    write_public_json(path, payload)


def _public_artifact(path: Path, *, claim: str, generated_at: datetime) -> EvidenceArtifact:
    return EvidenceArtifact(
        path=path.as_posix(),
        sha256=public_file_sha256(path),
        generated_at=generated_at,
        claim=claim,
    )


def execute_control(
    config: S0RuntimeConfig,
    *,
    repetition: int,
    source_revision: str,
) -> dict[str, Any]:
    client = BoundedHttpClient(timeout_seconds=config.http_timeout_seconds)
    root = W3CTraceContext.new_root()
    started_at = utc_now()
    queue_depth: list[float] = []
    queue_age: list[float] = []
    worker_active: list[float] = []
    run: dict[str, Any] | None = None
    lifecycle_run_id: str | None = None
    queue_started: float | None = None
    paced_inference: PacedInferenceResult | None = None
    failure: str | None = None
    cancelled = False

    with trace_span(
        "s0.low_load_control",
        parent=root,
        attributes={
            "evm.stage": "control",
            "evm.scenario.id": "S0",
            "evm.control.repetition": repetition,
        },
    ) as active:
        try:
            healthy_targets = assert_required_targets_healthy(config.prometheus_url)
            api_ready = client.json("GET", f"{config.api_url}/ready", trace_context=active.context)
            if api_ready.payload.get("status") != "ok":
                raise S0RuntimeError("s0_api_not_ready")
            if api_ready.payload.get("source_commit") != source_revision:
                raise S0RuntimeError("s0_api_revision_mismatch")
            worker = client.json(
                "GET",
                f"{config.api_url}/control-panel/v1/lifecycle-runs/worker",
                trace_context=active.context,
            )
            if worker.payload.get("status") != "online":
                raise S0RuntimeError("s0_worker_not_online")
            if worker.payload.get("source_commit") != source_revision:
                raise S0RuntimeError("s0_worker_revision_mismatch")
            intent_response = client.json(
                "GET",
                f"{config.api_url}/control-panel/v1/scenario-workloads/production-intents/current",
                trace_context=active.context,
            )
            intent = ScenarioProductionIntent.model_validate(intent_response.payload)
            if intent.state != "applied":
                raise S0RuntimeError(f"s0_serving_intent_not_applied:{intent.state}")
            serving_ready = client.json(
                "GET",
                f"{intent.target.endpoint}/ready",
                trace_context=active.context,
                timeout_seconds=15,
            )
            if serving_ready.payload.get("status") != "ready":
                raise S0RuntimeError("s0_serving_not_ready")
            if serving_ready.payload.get("runtime_source_commit") != source_revision:
                raise S0RuntimeError("s0_serving_runtime_revision_mismatch")

            cpu_query = (
                'rate(process_cpu_seconds_total{job="evm-api"}[1m]) / '
                f"{max(os.cpu_count() or 1, 1)}"
            )
            memory_query = 'process_resident_memory_bytes{job="evm-api"}'
            cpu_samples = [_prometheus_value(config.prometheus_url, cpu_query)]
            memory_samples = [_prometheus_value(config.prometheus_url, memory_query)]
            gpu_samples = [nvidia_smi_snapshot()]
            if gpu_samples[0].get("status") != "pass":
                raise S0RuntimeError("s0_gpu_snapshot_unavailable")

            created = client.json(
                "POST",
                f"{config.api_url}/control-panel/v1/lifecycle-runs",
                trace_context=active.context,
                payload={
                    "profile_id": config.profile_id,
                    "profile_version": config.profile_version,
                    "actor": "s0-control-runner",
                    "reason": f"S0 low-load control repetition {repetition}",
                    "dry_run": False,
                    "execution_mode": "stepwise",
                },
            )
            run = created.payload
            lifecycle_run_id = str(run["run_id"])
            if run.get("trace_id") != active.context.trace_id:
                raise S0RuntimeError("s0_lifecycle_trace_identity_mismatch")
            deadline = time.monotonic() + config.lifecycle_timeout_seconds
            while time.monotonic() < deadline:
                run = client.json(
                    "GET",
                    f"{config.api_url}/control-panel/v1/lifecycle-runs/{lifecycle_run_id}",
                    trace_context=active.context,
                ).payload
                state = str(run.get("state"))
                data_stage = _stage(run, "data_pipeline")
                queued = state == "queued" or data_stage.get("state") == "queued"
                if queued and queue_started is None:
                    queue_started = time.monotonic()
                queue_depth.append(1.0 if queued else 0.0)
                queue_age.append(
                    max(0.0, time.monotonic() - queue_started)
                    if queued and queue_started is not None
                    else 0.0
                )
                worker_state = client.json(
                    "GET",
                    f"{config.api_url}/control-panel/v1/lifecycle-runs/worker",
                    trace_context=active.context,
                ).payload
                worker_active.append(
                    1.0 if worker_state.get("current_run_id") == lifecycle_run_id else 0.0
                )
                if state in {"failed", "blocked", "cancelled", "rolled_back"}:
                    raise S0RuntimeError(
                        f"s0_lifecycle_failed:{state}:{run.get('failure_reason')}"
                    )
                if (
                    state == "paused"
                    and run.get("current_stage") == "model_training"
                    and data_stage.get("state") == "completed"
                ):
                    break
                time.sleep(config.poll_interval_seconds)
            else:
                raise S0RuntimeError("s0_lifecycle_data_stage_timeout")

            mlflow = MlflowRestClient(
                config.mlflow_url,
                traceparent=active.context.traceparent,
                tracestate=active.context.tracestate,
            )
            if not mlflow.health():
                raise S0RuntimeError("s0_mlflow_health_failed")

            def infer_sample(sample_selector: int) -> str:
                result = verify_production_inference(
                    intent,
                    trace_context=active.context,
                    sample_selector=sample_selector,
                )
                return str(result["sample_id"])

            paced_inference = execute_fixed_window_requests(
                target_rps=config.target_requests_per_second,
                duration_seconds=config.load_duration_seconds,
                request_count=config.serving_requests_per_run,
                seed=config.seed,
                request=infer_sample,
            )

            cpu_samples.append(_prometheus_value(config.prometheus_url, cpu_query))
            memory_samples.append(_prometheus_value(config.prometheus_url, memory_query))
            gpu_samples.append(nvidia_smi_snapshot())
            if gpu_samples[-1].get("status") != "pass":
                raise S0RuntimeError("s0_gpu_snapshot_unavailable")

            run = client.json(
                "POST",
                f"{config.api_url}/control-panel/v1/lifecycle-runs/{lifecycle_run_id}/cancel",
                trace_context=active.context,
                payload={
                    "actor": "s0-control-runner",
                    "reason": "S0 control reached the bounded data-to-serving observation boundary",
                    "expected_version": int(run["version"]),
                },
            ).payload
            cancelled = run.get("state") == "cancelled"
            if not cancelled:
                raise S0RuntimeError("s0_lifecycle_cleanup_not_confirmed")

            spans = wait_for_trace_stages(
                config.trace_file,
                active.context.trace_id,
                timeout_seconds=config.trace_timeout_seconds,
                poll_interval_seconds=config.poll_interval_seconds,
            )
            observed_stages = sorted({str(item["stage"]) for item in spans if item.get("stage")})
            wrong_revisions = [
                item
                for item in spans
                if item.get("stage") in REQUIRED_TRACE_STAGES
                and item.get("service_version") != source_revision
            ]
            if wrong_revisions:
                services = sorted({str(item.get("service_name")) for item in wrong_revisions})
                raise S0RuntimeError(f"s0_trace_runtime_revision_mismatch:{services}")

            data_stage = _stage(run, "data_pipeline")
            retry_attempts = max(0, int(data_stage.get("attempt") or 0) - 1)
            gpu_utilization = [float(item["utilization_percent"]) / 100 for item in gpu_samples]
            gpu_memory = [float(item["memory_used_mib"]) * 1024 * 1024 for item in gpu_samples]
            if paced_inference is None:
                raise S0RuntimeError("s0_inference_measurement_missing")
            inference_latencies = list(paced_inference.latencies_seconds)
            throughput = (
                len(inference_latencies) / config.load_duration_seconds
            )
            inference_measurement = InferenceMeasurementWindow(
                basis="fixed_duration_open_loop",
                target_requests_per_second=config.target_requests_per_second,
                declared_duration_seconds=config.load_duration_seconds,
                observed_elapsed_seconds=paced_inference.observed_elapsed_seconds,
                planned_request_count=config.serving_requests_per_run,
                completed_request_count=len(inference_latencies),
                scheduled_interval_seconds=1.0 / config.target_requests_per_second,
                max_request_start_lag_seconds=max(
                    paced_inference.request_start_lag_seconds,
                    default=0.0,
                ),
                seed=config.seed,
                seed_scope="deterministic_test_sample_selection",
                request_sequence_sha256=canonical_sha256(paced_inference.sample_ids),
                throughput_basis="completed_requests_per_declared_window",
            )
            metrics = [
                MetricObservation(
                    metric="request_latency_seconds",
                    unit="seconds",
                    sample_count=len(inference_latencies),
                    statistics=request_latency_statistics(inference_latencies),
                    query="client-observed round-trip latency for exact local CUDA /infer requests",
                ),
                _metric(
                    "request_throughput_per_second",
                    "requests_per_second",
                    [throughput],
                    "completed exact local CUDA /infer requests divided by the declared fixed window",
                ),
                _metric(
                    "inference_measurement_window_seconds",
                    "seconds",
                    [paced_inference.observed_elapsed_seconds],
                    "monotonic elapsed time for the declared fixed inference measurement window",
                ),
                _metric(
                    "queue_depth",
                    "runs",
                    queue_depth,
                    "exact LifecycleRun queued-state cardinality; historical ledger excluded",
                ),
                _metric(
                    "queue_oldest_age_seconds",
                    "seconds",
                    queue_age,
                    "monotonic age of the exact LifecycleRun while queued",
                ),
                _metric(
                    "worker_active_count",
                    "workers",
                    worker_active,
                    "worker.current_run_id exact-match cardinality for this LifecycleRun",
                ),
                _metric("cpu_utilization_ratio", "ratio", cpu_samples, cpu_query),
                _metric(
                    "memory_working_set_bytes",
                    "bytes",
                    memory_samples,
                    memory_query,
                ),
                _metric(
                    "gpu_utilization_ratio",
                    "ratio",
                    gpu_utilization,
                    "nvidia-smi physical GPU utilization sampled before and after control",
                ),
                _metric(
                    "gpu_memory_used_bytes",
                    "bytes",
                    gpu_memory,
                    "nvidia-smi physical GPU used memory sampled before and after control",
                ),
                _metric(
                    "load_generator_permit_wait_seconds",
                    "seconds",
                    client.permit_wait_seconds,
                    "bounded load-generator HTTP permit wait; not a database pool metric",
                ),
                _metric(
                    "retry_attempt_total",
                    "attempts",
                    [float(retry_attempts)],
                    "exact LifecycleRun data-stage attempt minus initial attempt",
                ),
            ]
            finished_at = utc_now()
            lifecycle_elapsed_seconds = (finished_at - started_at).total_seconds()
            raw = {
                "schema_version": "evm.scale_validation.s0_control_raw.v1",
                "scenario_id": "S0",
                "repetition": repetition,
                "started_at": started_at.isoformat(),
                "finished_at": finished_at.isoformat(),
                "trace_id": active.context.trace_id,
                "lifecycle_run_id": lifecycle_run_id,
                "lifecycle_terminal_state": run.get("state"),
                "identity": {
                    "source_revision": source_revision,
                    "data_digest": serving_ready.payload.get("data_identity_sha256"),
                    "model_digest": intent.model_artifact_sha256,
                    "runtime_digest": canonical_sha256(
                        {
                            "api": api_ready.payload.get("source_commit"),
                            "worker": worker.payload.get("source_commit"),
                            "serving": serving_ready.payload.get("runtime_source_commit"),
                            "target": intent.target.model_dump(mode="json"),
                        }
                    ),
                },
                "healthy_targets": healthy_targets,
                "trace": {
                    "required_stages": list(REQUIRED_TRACE_STAGES),
                    "observed_stages": observed_stages,
                    "span_count": len(spans),
                    "services": sorted(
                        {
                            f"{item.get('service_name')}@{item.get('service_version')}"
                            for item in spans
                            if item.get("stage") in REQUIRED_TRACE_STAGES
                        }
                    ),
                },
                "metrics": [item.model_dump(mode="json") for item in metrics],
                "timing": {
                    "lifecycle_elapsed_seconds": lifecycle_elapsed_seconds,
                    "inference_measurement": inference_measurement.model_dump(mode="json"),
                    "request_start_offsets_seconds": list(
                        paced_inference.request_start_offsets_seconds
                    ),
                    "request_start_lag_seconds": list(
                        paced_inference.request_start_lag_seconds
                    ),
                    "sample_selectors": list(paced_inference.sample_selectors),
                    "sample_ids": list(paced_inference.sample_ids),
                },
                "cleanup": {"lifecycle_cancelled": cancelled},
                "claim_boundary": (
                    "One local single-node low-load control using the existing runtime. "
                    "Permit wait is load-generator admission evidence, not database pool evidence."
                ),
            }
            private_path = config.private_evidence_root / f"control-{repetition}.json"
            _write_json(private_path, raw)
            return {
                "started_at": started_at,
                "finished_at": finished_at,
                "trace_id": active.context.trace_id,
                "identity": raw["identity"],
                "metrics": metrics,
                "lifecycle_elapsed_seconds": lifecycle_elapsed_seconds,
                "inference_measurement": inference_measurement,
                "trace": raw["trace"],
                "private_path": private_path,
                "private_sha256": file_sha256(private_path),
            }
        except Exception as exc:
            failure = f"{type(exc).__name__}:{exc}"
            raise
        finally:
            if lifecycle_run_id and run is not None and not cancelled:
                try:
                    latest = client.json(
                        "GET",
                        f"{config.api_url}/control-panel/v1/lifecycle-runs/{lifecycle_run_id}",
                        trace_context=active.context,
                    ).payload
                    if latest.get("state") not in {"completed", "cancelled", "rolled_back"}:
                        client.json(
                            "POST",
                            f"{config.api_url}/control-panel/v1/lifecycle-runs/{lifecycle_run_id}/cancel",
                            trace_context=active.context,
                            payload={
                                "actor": "s0-control-runner",
                                "reason": "S0 fail-closed cleanup after control interruption",
                                "expected_version": int(latest["version"]),
                            },
                        )
                except Exception as cleanup_exc:
                    failure = f"{failure};cleanup={type(cleanup_exc).__name__}:{cleanup_exc}"
            if failure:
                failure_path = config.private_evidence_root / f"control-{repetition}-failure.json"
                _write_json(
                    failure_path,
                    {
                        "schema_version": "evm.scale_validation.s0_control_failure.v1",
                        "scenario_id": "S0",
                        "repetition": repetition,
                        "started_at": started_at.isoformat(),
                        "failed_at": utc_now().isoformat(),
                        "trace_id": active.context.trace_id,
                        "lifecycle_run_id": lifecycle_run_id,
                        "error": failure,
                        "cleanup_requested": lifecycle_run_id is not None,
                    },
                )
            client.close()


def execute_suite(config: S0RuntimeConfig, *, source_revision: str) -> BenchmarkEvidence:
    if len(source_revision) != 40 or any(char not in "0123456789abcdef" for char in source_revision):
        raise S0RuntimeError("s0_source_revision_invalid")
    if config.repetitions < 3:
        raise S0RuntimeError("s0_requires_three_controls")
    configure_tracing("evm-s0-control-runner", service_version=runtime_service_version())
    controls = [
        execute_control(config, repetition=repetition, source_revision=source_revision)
        for repetition in range(1, config.repetitions + 1)
    ]
    identities = {canonical_sha256(control["identity"]) for control in controls}
    if len(identities) != 1:
        raise S0RuntimeError("s0_control_identity_changed")

    generated_at = utc_now()
    benchmark_runs: list[BenchmarkControlRun] = []
    for repetition, control in enumerate(controls, start=1):
        public_path = config.public_evidence_root / f"s0-low-load-control-{repetition}.json"
        _write_json(
            public_path,
            {
                "schema_version": "evm.scale_validation.s0_control_public.v1",
                "scenario_id": "S0",
                "repetition": repetition,
                "started_at": control["started_at"].isoformat(),
                "finished_at": control["finished_at"].isoformat(),
                "identity": control["identity"],
                "metrics": [item.model_dump(mode="json") for item in control["metrics"]],
                "timing": {
                    "lifecycle_elapsed_seconds": control["lifecycle_elapsed_seconds"],
                    "inference_measurement": control[
                        "inference_measurement"
                    ].model_dump(mode="json"),
                },
                "trace": {
                    "required_stages": list(REQUIRED_TRACE_STAGES),
                    "observed_stages": control["trace"]["observed_stages"],
                    "span_count": control["trace"]["span_count"],
                    "runtime_revision_converged": True,
                },
                "private_evidence_sha256": control["private_sha256"],
                "claim_boundary": (
                    "Existing local single-node runtime, controlled low load, and no customer "
                    "traffic. Exact run and trace identifiers remain in private evidence."
                ),
            },
        )
        artifact = _public_artifact(
            public_path,
            claim=f"S0 independent low-load control {repetition} passed the runtime contract.",
            generated_at=generated_at,
        )
        benchmark_runs.append(
            BenchmarkControlRun(
                repetition=repetition,
                started_at=control["started_at"],
                finished_at=control["finished_at"],
                lifecycle_elapsed_seconds=control["lifecycle_elapsed_seconds"],
                inference_measurement=control["inference_measurement"],
                metrics=control["metrics"],
                evidence_artifacts=[artifact],
            )
        )

    trace_path = config.public_evidence_root / "s0-cross-runtime-trace-summary.json"
    _write_json(
        trace_path,
        {
            "schema_version": "evm.scale_validation.s0_trace_summary.v1",
            "scenario_id": "S0",
            "generated_at": generated_at.isoformat(),
            "required_stages": list(REQUIRED_TRACE_STAGES),
            "controls": [
                {
                    "repetition": index,
                    "observed_stages": control["trace"]["observed_stages"],
                    "span_count": control["trace"]["span_count"],
                    "runtime_revision_converged": True,
                }
                for index, control in enumerate(controls, start=1)
            ],
            "metric_labels_bounded": True,
            "claim_boundary": "Exact trace IDs remain in private local evidence.",
        },
    )
    trace_artifact = _public_artifact(
        trace_path,
        claim="All controls observed the required existing-runtime trace stages.",
        generated_at=generated_at,
    )
    p95_values = [
        next(item for item in run.metrics if item.metric == "request_latency_seconds").statistics[
            "p95"
        ]
        for run in benchmark_runs
    ]
    throughput_values = [
        next(
            item for item in run.metrics if item.metric == "request_throughput_per_second"
        ).statistics["mean"]
        for run in benchmark_runs
    ]
    evidence = BenchmarkEvidence(
        schema_version=BENCHMARK_SCHEMA_VERSION,
        scenario_id="S0",
        benchmark_suite_id=f"s0-low-load-{generated_at.strftime('%Y%m%dT%H%M%SZ')}",
        generated_at=generated_at,
        identity=BenchmarkIdentity.model_validate(controls[0]["identity"]),
        environment=BenchmarkEnvironment(
            physical_nodes=1,
            cpu_logical_count=max(os.cpu_count() or 1, 1),
            ram_gib=total_ram_gib(),
            gpu_count=1,
            gpu_class="single discrete accelerator",
            load_generator_placement="co_located",
            environment_scope="local_single_node",
        ),
        load_profile=LoadProfile(
            mode="low_load_control",
            concurrency=1,
            target_requests_per_second=config.target_requests_per_second,
            warmup_seconds=0,
            duration_seconds=config.load_duration_seconds,
            seed=config.seed,
            arrival_model="fixed_rate_open_loop",
            request_count=config.serving_requests_per_run,
            seed_scope="deterministic_test_sample_selection",
        ),
        control_runs=benchmark_runs,
        trace_propagation=TracePropagationEvidence(
            required_stages=list(REQUIRED_TRACE_STAGES),
            observed_stages=list(REQUIRED_TRACE_STAGES),
            missing_propagation_count=0,
            metric_labels_bounded=True,
            trace_artifact=trace_artifact,
        ),
        variance={
            "request_latency_p95_cv": coefficient_of_variation(p95_values),
            "request_throughput_cv": coefficient_of_variation(throughput_values),
        },
        closure=BenchmarkClosure(decision="passed", blockers=[], completed_at=generated_at),
    )
    _write_json(
        config.public_evidence_root / "s0-low-load-benchmark-evidence.json",
        evidence.model_dump(mode="json"),
    )
    return evidence
