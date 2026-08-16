from __future__ import annotations

import hashlib
import json
import os
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from prometheus_client import Counter, Gauge, Histogram


DEFAULT_PROFILE_PATH = Path("configs/s2_bounded_queue_v1.toml")
ACTIVE_QUEUE_STATES = (
    "available",
    "retry_wait",
    "leased",
    "runtime_pending",
    "outcome_unknown",
)
TERMINAL_QUEUE_STATES = ("completed", "failed", "dlq", "expired", "cancelled")

QUEUE_ADMISSIONS = Counter(
    "evm_task_queue_admissions_total",
    "Task queue admission outcomes.",
    ("outcome", "reason"),
)
QUEUE_RETRY_AFTER_SECONDS = Histogram(
    "evm_task_queue_retry_after_seconds",
    "Retry-After values returned by bounded task admission.",
)
QUEUE_INGRESS_BODY_BYTES = Histogram(
    "evm_task_queue_ingress_body_bytes",
    "Bytes observed at the bounded task-ingress boundary.",
)
QUEUE_INGRESS_IN_FLIGHT_REQUESTS = Gauge(
    "evm_task_queue_ingress_in_flight_requests",
    "Task mutation requests currently buffered by one API process.",
)
QUEUE_INGRESS_IN_FLIGHT_BYTES = Gauge(
    "evm_task_queue_ingress_in_flight_bytes",
    "Task mutation body bytes currently reserved by one API process.",
)
QUEUE_DURABLE_DEPTH = Gauge(
    "evm_task_queue_durable_depth",
    "Durable task queue item count by bounded state class.",
    ("state",),
)
QUEUE_DURABLE_BYTES = Gauge(
    "evm_task_queue_durable_bytes",
    "Canonical UTF-8 bytes held by the durable task queue by state class.",
    ("state",),
)
QUEUE_OLDEST_AGE_SECONDS = Gauge(
    "evm_task_queue_oldest_age_seconds",
    "Age of the oldest active durable queue item.",
)
QUEUE_LOCAL_DEPTH = Gauge(
    "evm_task_queue_local_depth",
    "Process-local bounded queue depth.",
)
QUEUE_LOCAL_BYTES = Gauge(
    "evm_task_queue_local_bytes",
    "Process-local bounded queue canonical payload bytes.",
)
QUEUE_IN_FLIGHT = Gauge(
    "evm_task_queue_in_flight",
    "Task queue work currently executing by resource class.",
    ("resource_class",),
)
QUEUE_WORKER_CAPACITY = Gauge(
    "evm_task_queue_worker_capacity",
    "Configured worker capacity by resource class.",
    ("resource_class",),
)
QUEUE_WORKER_LIVE_CONSUMERS = Gauge(
    "evm_task_queue_worker_live_consumers",
    "Live task queue consumers by resource class.",
    ("resource_class",),
)
QUEUE_CONSUMER_FAILURES = Counter(
    "evm_task_queue_consumer_failures_total",
    "Consumer failures caught by the task queue supervisor.",
    ("resource_class", "failure_class"),
)
QUEUE_CPU_SCALE_EVENTS = Counter(
    "evm_task_queue_cpu_scale_events_total",
    "Bounded CPU consumer target changes.",
    ("direction",),
)
QUEUE_LEASE_RENEWALS = Counter(
    "evm_task_queue_lease_renewals_total",
    "Lease renewal outcomes for prefetched and executing work.",
    ("outcome",),
)
QUEUE_ADMISSION_WAIT_SECONDS = Histogram(
    "evm_task_queue_admission_wait_seconds",
    "Time spent in the durable admission transaction.",
)
QUEUE_RETRIES = Counter(
    "evm_task_queue_retries_total",
    "Task queue retry outcomes by bounded failure class.",
    ("outcome", "failure_class"),
)
QUEUE_TERMINALS = Counter(
    "evm_task_queue_terminal_total",
    "Task queue terminal outcomes.",
    ("state", "reason"),
)
QUEUE_DLQ = Counter(
    "evm_task_queue_dlq_total",
    "Task queue items quarantined in the durable DLQ.",
    ("reason",),
)
QUEUE_PROCESS_RSS_BYTES = Gauge(
    "evm_task_queue_process_rss_bytes",
    "Resident memory used by the task queue worker and executor process tree.",
)
QUEUE_PROCESS_RSS_SLOPE_BYTES_PER_MINUTE = Gauge(
    "evm_task_queue_process_rss_slope_bytes_per_minute",
    "One-minute worker and executor process-tree RSS slope.",
)
QUEUE_WORK_SECONDS = Histogram(
    "evm_task_queue_work_seconds",
    "Task queue execution duration by terminal class.",
    ("outcome",),
)
QUEUE_HISTORY_ROWS = Gauge(
    "evm_task_queue_history_rows",
    "Persisted task-control rows by bounded history class.",
    ("history_class",),
)
QUEUE_HISTORY_BYTES = Gauge(
    "evm_task_queue_history_bytes",
    "Approximate persisted task-control bytes by bounded history class.",
    ("history_class",),
)
QUEUE_COMPACTIONS = Counter(
    "evm_task_queue_compactions_total",
    "Rows removed by bounded task-control history compaction.",
    ("history_class",),
)


@dataclass(frozen=True)
class AdmissionQueueConfig:
    profile_version: str
    durable_max_depth: int
    durable_max_bytes: int
    max_item_bytes: int
    ingress_max_body_bytes: int
    ingress_max_concurrent_requests: int
    ingress_max_inflight_bytes: int
    ingress_max_chunks: int
    max_age_seconds: float
    admission_wait_seconds: float
    retry_after_seconds: int
    pending_max_depth: int
    pending_max_bytes: int
    pending_max_age_seconds: float
    local_max_depth: int
    local_max_bytes: int
    work_timeout_seconds: float
    cpu_workers_min: int
    cpu_workers_max: int
    gpu_workers: int
    lease_seconds: float
    lease_renew_interval_seconds: float
    runtime_poll_interval_seconds: float
    runtime_poll_batch_size: int
    runtime_terminal_timeout_seconds: float
    cpu_downstream_max_outstanding: int
    gpu_downstream_max_outstanding: int
    max_attempts: int
    backoff_base_seconds: float
    backoff_max_seconds: float
    jitter_ratio: float
    global_retry_budget: int
    retry_budget_window_seconds: float
    retry_budget_scope: str
    drain_timeout_seconds: float
    rss_cap_bytes: int
    rss_slope_tolerance_bytes_per_minute: int
    rss_slope_warmup_seconds: float
    rss_slope_window_seconds: float
    poll_interval_seconds: float
    metrics_port: int
    terminal_queue_max_rows: int
    terminal_queue_max_age_seconds: float
    task_history_max_terminal_rows: int
    idempotency_tombstone_max_rows: int
    idempotency_tombstone_retention_seconds: float
    compaction_batch_size: int

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> AdmissionQueueConfig:
        profile = payload.get("profile", {})
        durable = payload.get("durable_queue", {})
        local = payload.get("local_queue", {})
        worker = payload.get("worker", {})
        retry = payload.get("retry", {})
        limits = payload.get("resource_limits", {})
        retention = payload.get("retention", {})
        config = cls(
            profile_version=str(profile["version"]),
            durable_max_depth=int(durable["max_depth"]),
            durable_max_bytes=int(durable["max_bytes"]),
            max_item_bytes=int(durable["max_item_bytes"]),
            ingress_max_body_bytes=int(durable["ingress_max_body_bytes"]),
            ingress_max_concurrent_requests=int(
                durable["ingress_max_concurrent_requests"]
            ),
            ingress_max_inflight_bytes=int(durable["ingress_max_inflight_bytes"]),
            ingress_max_chunks=int(durable["ingress_max_chunks"]),
            max_age_seconds=float(durable["max_age_seconds"]),
            admission_wait_seconds=float(durable["admission_wait_seconds"]),
            retry_after_seconds=int(durable["retry_after_seconds"]),
            pending_max_depth=int(durable["pending_max_depth"]),
            pending_max_bytes=int(durable["pending_max_bytes"]),
            pending_max_age_seconds=float(durable["pending_max_age_seconds"]),
            local_max_depth=int(local["max_depth"]),
            local_max_bytes=int(local["max_bytes"]),
            work_timeout_seconds=float(worker["work_timeout_seconds"]),
            cpu_workers_min=int(worker["cpu_workers_min"]),
            cpu_workers_max=int(worker["cpu_workers_max"]),
            gpu_workers=int(worker["gpu_workers"]),
            lease_seconds=float(worker["lease_seconds"]),
            lease_renew_interval_seconds=float(worker["lease_renew_interval_seconds"]),
            runtime_poll_interval_seconds=float(worker["runtime_poll_interval_seconds"]),
            runtime_poll_batch_size=int(worker["runtime_poll_batch_size"]),
            runtime_terminal_timeout_seconds=float(
                worker["runtime_terminal_timeout_seconds"]
            ),
            cpu_downstream_max_outstanding=int(
                worker["cpu_downstream_max_outstanding"]
            ),
            gpu_downstream_max_outstanding=int(
                worker["gpu_downstream_max_outstanding"]
            ),
            max_attempts=int(retry["max_attempts"]),
            backoff_base_seconds=float(retry["backoff_base_seconds"]),
            backoff_max_seconds=float(retry["backoff_max_seconds"]),
            jitter_ratio=float(retry["jitter_ratio"]),
            global_retry_budget=int(retry["global_budget"]),
            retry_budget_window_seconds=float(retry["budget_window_seconds"]),
            retry_budget_scope=str(retry["budget_scope"]),
            drain_timeout_seconds=float(worker["drain_timeout_seconds"]),
            rss_cap_bytes=int(limits["rss_cap_bytes"]),
            rss_slope_tolerance_bytes_per_minute=int(
                limits["rss_slope_tolerance_bytes_per_minute"]
            ),
            rss_slope_warmup_seconds=float(limits["rss_slope_warmup_seconds"]),
            rss_slope_window_seconds=float(limits["rss_slope_window_seconds"]),
            poll_interval_seconds=float(worker["poll_interval_seconds"]),
            metrics_port=int(worker["metrics_port"]),
            terminal_queue_max_rows=int(retention["terminal_queue_max_rows"]),
            terminal_queue_max_age_seconds=float(
                retention["terminal_queue_max_age_seconds"]
            ),
            task_history_max_terminal_rows=int(
                retention["task_history_max_terminal_rows"]
            ),
            idempotency_tombstone_max_rows=int(
                retention["idempotency_tombstone_max_rows"]
            ),
            idempotency_tombstone_retention_seconds=float(
                retention["idempotency_tombstone_retention_seconds"]
            ),
            compaction_batch_size=int(retention["compaction_batch_size"]),
        )
        config.validate()
        return config

    @classmethod
    def from_path(cls, path: str | Path) -> AdmissionQueueConfig:
        with Path(path).open("rb") as handle:
            return cls.from_mapping(tomllib.load(handle))

    def validate(self) -> None:
        positive = {
            "durable_max_depth": self.durable_max_depth,
            "durable_max_bytes": self.durable_max_bytes,
            "max_item_bytes": self.max_item_bytes,
            "ingress_max_body_bytes": self.ingress_max_body_bytes,
            "ingress_max_concurrent_requests": self.ingress_max_concurrent_requests,
            "ingress_max_inflight_bytes": self.ingress_max_inflight_bytes,
            "ingress_max_chunks": self.ingress_max_chunks,
            "max_age_seconds": self.max_age_seconds,
            "admission_wait_seconds": self.admission_wait_seconds,
            "retry_after_seconds": self.retry_after_seconds,
            "pending_max_depth": self.pending_max_depth,
            "pending_max_bytes": self.pending_max_bytes,
            "pending_max_age_seconds": self.pending_max_age_seconds,
            "local_max_depth": self.local_max_depth,
            "local_max_bytes": self.local_max_bytes,
            "work_timeout_seconds": self.work_timeout_seconds,
            "cpu_workers_min": self.cpu_workers_min,
            "cpu_workers_max": self.cpu_workers_max,
            "gpu_workers": self.gpu_workers,
            "lease_seconds": self.lease_seconds,
            "lease_renew_interval_seconds": self.lease_renew_interval_seconds,
            "runtime_poll_interval_seconds": self.runtime_poll_interval_seconds,
            "runtime_poll_batch_size": self.runtime_poll_batch_size,
            "runtime_terminal_timeout_seconds": self.runtime_terminal_timeout_seconds,
            "cpu_downstream_max_outstanding": self.cpu_downstream_max_outstanding,
            "gpu_downstream_max_outstanding": self.gpu_downstream_max_outstanding,
            "max_attempts": self.max_attempts,
            "backoff_base_seconds": self.backoff_base_seconds,
            "backoff_max_seconds": self.backoff_max_seconds,
            "global_retry_budget": self.global_retry_budget,
            "retry_budget_window_seconds": self.retry_budget_window_seconds,
            "drain_timeout_seconds": self.drain_timeout_seconds,
            "rss_cap_bytes": self.rss_cap_bytes,
            "rss_slope_window_seconds": self.rss_slope_window_seconds,
            "poll_interval_seconds": self.poll_interval_seconds,
            "metrics_port": self.metrics_port,
            "terminal_queue_max_rows": self.terminal_queue_max_rows,
            "terminal_queue_max_age_seconds": self.terminal_queue_max_age_seconds,
            "task_history_max_terminal_rows": self.task_history_max_terminal_rows,
            "idempotency_tombstone_max_rows": self.idempotency_tombstone_max_rows,
            "idempotency_tombstone_retention_seconds": (
                self.idempotency_tombstone_retention_seconds
            ),
            "compaction_batch_size": self.compaction_batch_size,
        }
        invalid = [name for name, value in positive.items() if value <= 0]
        if invalid:
            raise ValueError(f"S2 queue configuration values must be positive: {invalid}")
        if self.max_item_bytes > self.local_max_bytes:
            raise ValueError("max_item_bytes cannot exceed local_max_bytes")
        if self.local_max_bytes > self.durable_max_bytes:
            raise ValueError("local_max_bytes cannot exceed durable_max_bytes")
        if self.local_max_depth > self.durable_max_depth:
            raise ValueError("local_max_depth cannot exceed durable_max_depth")
        if self.cpu_workers_min > self.cpu_workers_max:
            raise ValueError("cpu_workers_min cannot exceed cpu_workers_max")
        if self.gpu_workers != 1:
            raise ValueError("the local single-GPU S2 contract requires gpu_workers=1")
        if self.backoff_base_seconds > self.backoff_max_seconds:
            raise ValueError("backoff_base_seconds cannot exceed backoff_max_seconds")
        if not 0 <= self.jitter_ratio <= 1:
            raise ValueError("jitter_ratio must be between 0 and 1")
        if self.rss_slope_tolerance_bytes_per_minute < 0:
            raise ValueError("rss_slope_tolerance_bytes_per_minute cannot be negative")
        if self.rss_slope_warmup_seconds < 0:
            raise ValueError("rss_slope_warmup_seconds cannot be negative")
        if self.rss_slope_window_seconds <= 0:
            raise ValueError("rss_slope_window_seconds must be positive")
        if self.ingress_max_body_bytes > self.max_item_bytes:
            raise ValueError("ingress_max_body_bytes cannot exceed max_item_bytes")
        if self.ingress_max_body_bytes > self.ingress_max_inflight_bytes:
            raise ValueError(
                "ingress_max_body_bytes cannot exceed ingress_max_inflight_bytes"
            )
        if self.local_max_depth > self.durable_max_depth:
            raise ValueError("local queue depth cannot exceed durable queue depth")
        if self.local_max_bytes > self.durable_max_bytes:
            raise ValueError("local queue bytes cannot exceed durable queue bytes")
        if self.lease_renew_interval_seconds >= self.lease_seconds:
            raise ValueError("lease renewal interval must be shorter than the lease")
        if self.gpu_downstream_max_outstanding != 1:
            raise ValueError(
                "the local single-GPU S2 contract requires one downstream GPU runtime"
            )
        if self.cpu_downstream_max_outstanding < self.cpu_workers_max:
            raise ValueError(
                "CPU downstream outstanding capacity cannot be below CPU worker maximum"
            )
        if not self.retry_budget_scope or len(self.retry_budget_scope) > 128:
            raise ValueError("retry_budget_scope must be between 1 and 128 characters")
        retention_positive = {
            "terminal_queue_max_rows": self.terminal_queue_max_rows,
            "terminal_queue_max_age_seconds": self.terminal_queue_max_age_seconds,
            "task_history_max_terminal_rows": self.task_history_max_terminal_rows,
            "idempotency_tombstone_max_rows": self.idempotency_tombstone_max_rows,
            "idempotency_tombstone_retention_seconds": (
                self.idempotency_tombstone_retention_seconds
            ),
            "compaction_batch_size": self.compaction_batch_size,
        }
        invalid_retention = [
            name for name, value in retention_positive.items() if value <= 0
        ]
        if invalid_retention:
            raise ValueError(
                f"S2 retention configuration values must be positive: {invalid_retention}"
            )

    def public_dict(self) -> dict[str, int | float | str]:
        return {
            "profile_version": self.profile_version,
            "durable_max_depth": self.durable_max_depth,
            "durable_max_bytes": self.durable_max_bytes,
            "max_item_bytes": self.max_item_bytes,
            "ingress_max_body_bytes": self.ingress_max_body_bytes,
            "ingress_max_concurrent_requests": self.ingress_max_concurrent_requests,
            "ingress_max_inflight_bytes": self.ingress_max_inflight_bytes,
            "ingress_max_chunks": self.ingress_max_chunks,
            "max_age_seconds": self.max_age_seconds,
            "admission_wait_seconds": self.admission_wait_seconds,
            "retry_after_seconds": self.retry_after_seconds,
            "pending_max_depth": self.pending_max_depth,
            "pending_max_bytes": self.pending_max_bytes,
            "pending_max_age_seconds": self.pending_max_age_seconds,
            "local_max_depth": self.local_max_depth,
            "local_max_bytes": self.local_max_bytes,
            "work_timeout_seconds": self.work_timeout_seconds,
            "cpu_workers_min": self.cpu_workers_min,
            "cpu_workers_max": self.cpu_workers_max,
            "gpu_workers": self.gpu_workers,
            "lease_seconds": self.lease_seconds,
            "lease_renew_interval_seconds": self.lease_renew_interval_seconds,
            "runtime_poll_interval_seconds": self.runtime_poll_interval_seconds,
            "runtime_poll_batch_size": self.runtime_poll_batch_size,
            "runtime_terminal_timeout_seconds": self.runtime_terminal_timeout_seconds,
            "cpu_downstream_max_outstanding": self.cpu_downstream_max_outstanding,
            "gpu_downstream_max_outstanding": self.gpu_downstream_max_outstanding,
            "max_attempts": self.max_attempts,
            "backoff_base_seconds": self.backoff_base_seconds,
            "backoff_max_seconds": self.backoff_max_seconds,
            "jitter_ratio": self.jitter_ratio,
            "global_retry_budget": self.global_retry_budget,
            "retry_budget_window_seconds": self.retry_budget_window_seconds,
            "retry_budget_scope": self.retry_budget_scope,
            "drain_timeout_seconds": self.drain_timeout_seconds,
            "rss_cap_bytes": self.rss_cap_bytes,
            "rss_slope_tolerance_bytes_per_minute": self.rss_slope_tolerance_bytes_per_minute,
            "rss_slope_warmup_seconds": self.rss_slope_warmup_seconds,
            "rss_slope_window_seconds": self.rss_slope_window_seconds,
            "poll_interval_seconds": self.poll_interval_seconds,
            "metrics_port": self.metrics_port,
            "terminal_queue_max_rows": self.terminal_queue_max_rows,
            "terminal_queue_max_age_seconds": self.terminal_queue_max_age_seconds,
            "task_history_max_terminal_rows": self.task_history_max_terminal_rows,
            "idempotency_tombstone_max_rows": self.idempotency_tombstone_max_rows,
            "idempotency_tombstone_retention_seconds": (
                self.idempotency_tombstone_retention_seconds
            ),
            "compaction_batch_size": self.compaction_batch_size,
        }

    @property
    def sha256(self) -> str:
        return hashlib.sha256(canonical_json_bytes(self.public_dict())).hexdigest()


def canonical_json_bytes(payload: object) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def canonical_payload_size(payload: object) -> int:
    return len(canonical_json_bytes(payload))


def admission_queue_mode() -> str:
    mode = os.getenv("EVM_TASK_ADMISSION_MODE", "legacy").strip().lower()
    if mode not in {"legacy", "durable"}:
        raise ValueError(f"unsupported task admission mode: {mode}")
    return mode


def load_admission_queue_config() -> AdmissionQueueConfig:
    path = Path(os.getenv("EVM_TASK_QUEUE_CONFIG", str(DEFAULT_PROFILE_PATH)))
    return AdmissionQueueConfig.from_path(path)


def priority_value(priority: str) -> int:
    return {"low": 10, "normal": 20, "high": 30, "urgent": 40}.get(priority, 20)


def task_resource_class(task_payload: Mapping[str, Any]) -> str:
    config_payload = task_payload.get("config_payload")
    explicit = (
        str(config_payload.get("resource_class", "")).strip().lower()
        if isinstance(config_payload, Mapping)
        else ""
    )
    profile = str(task_payload.get("resource_profile", "")).strip().lower()
    gpu_profile_markers = ("gpu", "cuda", "rtx", "accelerator")
    return (
        "gpu"
        if explicit == "gpu" or any(marker in profile for marker in gpu_profile_markers)
        else "cpu"
    )


def bounded_queue_metric_reason(value: object) -> str:
    candidate = str(value or "unknown").strip().lower()
    exact = {
        "within_bounds",
        "idempotency",
        "pending_approval",
        "item_size_limit",
        "ingress_body_limit",
        "ingress_chunk_limit",
        "ingress_aggregate_limit",
        "invalid_content_length",
        "durable_depth_limit",
        "durable_bytes_limit",
        "pending_depth_limit",
        "pending_bytes_limit",
        "idempotency_capacity_limit",
        "admission_lock_timeout",
        "deadline_exceeded",
        "runtime_terminal_timeout",
        "owner_lost",
        "work_timeout",
        "control_plane_pool_timeout",
        "airflow_api_unavailable",
        "airflow_api_rejected",
        "airflow_dag_run_conflict",
        "invalid_payload",
        "dependency_503",
    }
    if candidate in exact:
        return candidate
    for prefix in (
        "runtime_terminal:",
        "permanent:",
        "attempts_exhausted:",
        "retry_budget_exhausted:",
    ):
        if candidate.startswith(prefix):
            return prefix[:-1]
    return "other"
