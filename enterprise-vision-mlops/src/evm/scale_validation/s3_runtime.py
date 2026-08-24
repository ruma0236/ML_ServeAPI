from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
import socket
import subprocess
import sys
import time
import tomllib
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation, ROUND_HALF_EVEN
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping, Sequence, TextIO
from uuid import uuid4

import httpx
import numpy as np
import psutil
import requests
from prometheus_client.parser import text_string_to_metric_families

from evm.scale_validation.s3_higgs import file_sha256


PROBE_FAMILIES = (
    "logistic",
    "probabilistic",
    "online-linear",
    "branch-heavy",
    "incremental",
)
REQUIRED_TRACE_SPANS = {
    "POST /control-panel/v1/scenario-workloads/capacity-probes/predict",
    "s3.capacity.admission",
    "s3.capacity.worker",
    "s3.capacity.validation",
    "s3.capacity.transform",
    "s3.capacity.prediction",
}
REQUIRED_ADMISSION_TRACE_SPANS = {
    "POST /control-panel/v1/scenario-workloads/capacity-probes/predict",
    "s3.capacity.admission",
}
TERMINAL_GAUGES = (
    "evm_s3_capacity_executor_queue_depth",
    "evm_s3_capacity_executor_queue_bytes",
    "evm_s3_capacity_executor_in_flight",
    "evm_s3_capacity_executor_in_flight_bytes",
    "evm_s3_capacity_executor_outstanding",
    "evm_s3_capacity_executor_outstanding_bytes",
)
CLAIM_BOUNDARY = (
    "Controlled HIGGS lightweight CPU/API capacity on one local physical node. "
    "No customer traffic, production SLA, physical multi-node or multi-zone HA, "
    "stateful HA/DR, multi-GPU, business A/B, or full-terabyte claim."
)
PUBLIC_PROJECTION_DECIMAL_PLACES = 12
_PUBLIC_PROJECTION_QUANTUM = Decimal("1e-12")


class S3RuntimeError(RuntimeError):
    pass


@dataclass(frozen=True)
class S3LoadPoint:
    mode: str
    probe_family: str
    load: float
    api_replicas: int
    cpu_workers: int
    matrix_scope: str

    @property
    def point_id(self) -> str:
        load = str(int(self.load)) if self.load.is_integer() else f"{self.load:g}"
        return (
            f"{self.matrix_scope}-{self.mode}-{self.probe_family}-load-{load}-"
            f"api-{self.api_replicas}-cpu-{self.cpu_workers}"
        )


@dataclass(frozen=True)
class S3RuntimeConfig:
    path: Path
    sha256: str
    preparation_config_sha256: str
    dataset_version: str
    seed: int
    registry_path: Path
    replay_features_path: Path
    max_outstanding_per_replica: int
    max_outstanding_bytes_per_replica: int
    max_request_bytes: int
    admission_wait_seconds: float
    request_timeout_seconds: float
    retry_after_seconds: int
    client_max_in_flight: int
    resource_sample_interval_seconds: float
    resource_metrics_timeout_seconds: float
    resource_metrics_max_consecutive_failures: int
    trace_flush_seconds: float
    trace_poll_interval_seconds: float
    prometheus_scrape_interval_seconds: float
    repetitions: int
    closed_warmup_seconds: float
    closed_measurement_seconds: float
    closed_cooldown_seconds: float
    closed_steps: tuple[int, ...]
    closed_baseline_api_replicas: int
    closed_baseline_cpu_workers: int
    open_warmup_seconds: float
    open_measurement_seconds: float
    open_cooldown_seconds: float
    open_rates: tuple[float, ...]
    open_baseline_api_replicas: int
    open_baseline_cpu_workers: int
    topology_probe_family: str
    topology_closed_concurrency: int
    topology_open_rate: float
    topology_api_replicas: tuple[int, ...]
    topology_cpu_workers: tuple[int, ...]
    maximum_error_rate: float
    maximum_p99_ms: float
    maximum_host_cpu_percent: float
    maximum_process_tree_rss_bytes: int
    maximum_load_generator_start_lag_ms: float
    queue_drain_timeout_seconds: float
    maximum_queue_wait_seconds: float
    capacity_safety_factor: float
    prior_depth: int
    rollback_depth: int
    allow_automatic_increase: bool

    @classmethod
    def from_path(cls, path: Path, *, data_root: Path) -> "S3RuntimeConfig":
        resolved = path.resolve()
        with resolved.open("rb") as handle:
            payload = tomllib.load(handle)
        if payload.get("schema_version") != "evm.s3_capacity_runtime.v1":
            raise S3RuntimeError("s3_runtime_config_schema_invalid")
        paths = _section(payload, "paths")
        execution = _section(payload, "execution")
        closed = _section(payload, "closed_concurrency")
        opened = _section(payload, "open_arrival_rate")
        topology = _section(payload, "topology_comparison")
        guardrails = _section(payload, "guardrails")
        recalculation = _section(payload, "capacity_recalculation")
        closed_repetitions = int(closed.get("repetitions", 0))
        open_repetitions = int(opened.get("repetitions", 0))
        if closed_repetitions != 3 or open_repetitions != 3:
            raise S3RuntimeError("s3_requires_three_independent_repetitions")
        config = cls(
            path=resolved,
            sha256=file_sha256(resolved),
            preparation_config_sha256=_sha256(
                payload.get("preparation_config_sha256"),
                "preparation_config_sha256",
            ),
            dataset_version=str(payload.get("dataset_version") or ""),
            seed=int(payload.get("seed", 0)),
            registry_path=data_root / str(paths["registry_relative_path"]),
            replay_features_path=(
                data_root / str(paths["replay_features_relative_path"])
            ),
            max_outstanding_per_replica=int(
                execution["max_outstanding_per_replica"]
            ),
            max_outstanding_bytes_per_replica=int(
                execution["max_outstanding_bytes_per_replica"]
            ),
            max_request_bytes=int(execution["max_request_bytes"]),
            admission_wait_seconds=float(execution["admission_wait_seconds"]),
            request_timeout_seconds=float(execution["request_timeout_seconds"]),
            retry_after_seconds=int(execution["retry_after_seconds"]),
            client_max_in_flight=int(execution["client_max_in_flight"]),
            resource_sample_interval_seconds=float(
                execution["resource_sample_interval_seconds"]
            ),
            resource_metrics_timeout_seconds=float(
                execution.get("resource_metrics_timeout_seconds", 1.0)
            ),
            resource_metrics_max_consecutive_failures=int(
                execution.get("resource_metrics_max_consecutive_failures", 3)
            ),
            trace_flush_seconds=float(execution["trace_flush_seconds"]),
            trace_poll_interval_seconds=float(
                execution["trace_poll_interval_seconds"]
            ),
            prometheus_scrape_interval_seconds=float(
                execution["prometheus_scrape_interval_seconds"]
            ),
            repetitions=closed_repetitions,
            closed_warmup_seconds=float(closed["warmup_seconds"]),
            closed_measurement_seconds=float(closed["measurement_seconds"]),
            closed_cooldown_seconds=float(closed["cooldown_seconds"]),
            closed_steps=tuple(int(value) for value in closed["steps"]),
            closed_baseline_api_replicas=int(closed["baseline_api_replicas"]),
            closed_baseline_cpu_workers=int(closed["baseline_cpu_workers"]),
            open_warmup_seconds=float(opened["warmup_seconds"]),
            open_measurement_seconds=float(opened["measurement_seconds"]),
            open_cooldown_seconds=float(opened["cooldown_seconds"]),
            open_rates=tuple(float(value) for value in opened["requests_per_second"]),
            open_baseline_api_replicas=int(opened["baseline_api_replicas"]),
            open_baseline_cpu_workers=int(opened["baseline_cpu_workers"]),
            topology_probe_family=str(topology["probe_family"]),
            topology_closed_concurrency=int(topology["closed_concurrency"]),
            topology_open_rate=float(topology["open_requests_per_second"]),
            topology_api_replicas=tuple(
                int(value) for value in topology["api_replicas"]
            ),
            topology_cpu_workers=tuple(
                int(value) for value in topology["cpu_workers"]
            ),
            maximum_error_rate=float(guardrails["maximum_error_rate"]),
            maximum_p99_ms=float(guardrails["maximum_p99_ms"]),
            maximum_host_cpu_percent=float(
                guardrails["maximum_host_cpu_percent"]
            ),
            maximum_process_tree_rss_bytes=int(
                guardrails["maximum_process_tree_rss_bytes"]
            ),
            maximum_load_generator_start_lag_ms=float(
                guardrails["maximum_load_generator_start_lag_ms"]
            ),
            queue_drain_timeout_seconds=float(
                guardrails["queue_drain_timeout_seconds"]
            ),
            maximum_queue_wait_seconds=float(
                recalculation["maximum_queue_wait_seconds"]
            ),
            capacity_safety_factor=float(recalculation["safety_factor"]),
            prior_depth=int(recalculation["prior_depth"]),
            rollback_depth=int(recalculation["rollback_depth"]),
            allow_automatic_increase=bool(
                recalculation["allow_automatic_increase"]
            ),
        )
        config.validate()
        return config

    def validate(self) -> None:
        if not self.dataset_version or self.seed <= 0:
            raise S3RuntimeError("s3_runtime_identity_invalid")
        if self.topology_probe_family not in PROBE_FAMILIES:
            raise S3RuntimeError("s3_topology_probe_family_invalid")
        positive_ints = (
            self.max_outstanding_per_replica,
            self.max_outstanding_bytes_per_replica,
            self.max_request_bytes,
            self.retry_after_seconds,
            self.client_max_in_flight,
            self.resource_metrics_max_consecutive_failures,
            self.prior_depth,
            self.rollback_depth,
            *self.closed_steps,
            *self.topology_api_replicas,
            *self.topology_cpu_workers,
        )
        positive_floats = (
            self.request_timeout_seconds,
            self.resource_sample_interval_seconds,
            self.resource_metrics_timeout_seconds,
            self.trace_flush_seconds,
            self.trace_poll_interval_seconds,
            self.prometheus_scrape_interval_seconds,
            self.closed_warmup_seconds,
            self.closed_measurement_seconds,
            self.open_warmup_seconds,
            self.open_measurement_seconds,
            self.queue_drain_timeout_seconds,
            self.maximum_queue_wait_seconds,
            *self.open_rates,
        )
        if min(positive_ints) <= 0 or min(positive_floats) <= 0:
            raise S3RuntimeError("s3_runtime_positive_bound_invalid")
        if self.max_outstanding_per_replica < max(self.topology_cpu_workers):
            raise S3RuntimeError("s3_runtime_outstanding_lower_than_workers")
        if self.max_outstanding_bytes_per_replica < self.max_request_bytes:
            raise S3RuntimeError("s3_runtime_byte_bound_invalid")
        if not 0 <= self.admission_wait_seconds <= 30:
            raise S3RuntimeError("s3_runtime_admission_wait_invalid")
        if not 0 < self.maximum_error_rate < 1:
            raise S3RuntimeError("s3_runtime_error_guardrail_invalid")
        if not 0 < self.capacity_safety_factor <= 1:
            raise S3RuntimeError("s3_runtime_safety_factor_invalid")
        for values in (
            self.closed_steps,
            self.open_rates,
            self.topology_api_replicas,
            self.topology_cpu_workers,
        ):
            if tuple(sorted(set(values))) != values:
                raise S3RuntimeError("s3_runtime_steps_must_be_unique_and_sorted")

    def assert_frozen(self) -> None:
        observed = file_sha256(self.path)
        if observed != self.sha256:
            raise S3RuntimeError(
                f"s3_runtime_config_changed:{self.sha256}:{observed}"
            )

    def points(self) -> list[S3LoadPoint]:
        points: list[S3LoadPoint] = []
        for family in PROBE_FAMILIES:
            points.extend(
                S3LoadPoint(
                    mode="closed",
                    probe_family=family,
                    load=float(load),
                    api_replicas=self.closed_baseline_api_replicas,
                    cpu_workers=self.closed_baseline_cpu_workers,
                    matrix_scope="baseline",
                )
                for load in self.closed_steps
            )
            points.extend(
                S3LoadPoint(
                    mode="open",
                    probe_family=family,
                    load=float(load),
                    api_replicas=self.open_baseline_api_replicas,
                    cpu_workers=self.open_baseline_cpu_workers,
                    matrix_scope="baseline",
                )
                for load in self.open_rates
            )
        for api_replicas in self.topology_api_replicas:
            for cpu_workers in self.topology_cpu_workers:
                points.extend(
                    [
                        S3LoadPoint(
                            mode="closed",
                            probe_family=self.topology_probe_family,
                            load=float(self.topology_closed_concurrency),
                            api_replicas=api_replicas,
                            cpu_workers=cpu_workers,
                            matrix_scope="topology",
                        ),
                        S3LoadPoint(
                            mode="open",
                            probe_family=self.topology_probe_family,
                            load=self.topology_open_rate,
                            api_replicas=api_replicas,
                            cpu_workers=cpu_workers,
                            matrix_scope="topology",
                        ),
                    ]
                )
        unique: dict[tuple[str, str, float, int, int], S3LoadPoint] = {}
        for point in points:
            key = (
                point.mode,
                point.probe_family,
                point.load,
                point.api_replicas,
                point.cpu_workers,
            )
            unique.setdefault(key, point)
        return list(unique.values())


@dataclass
class ManagedProcess:
    process: subprocess.Popen[str]
    label: str
    stdout_path: Path
    stderr_path: Path
    stdout_stream: TextIO
    stderr_stream: TextIO

    @property
    def pid(self) -> int:
        return int(self.process.pid)

    def stop(self, *, force: bool = False) -> list[int]:
        stopped = terminate_process_tree(self.pid, force=force)
        self.process.wait(timeout=15)
        self.stdout_stream.close()
        self.stderr_stream.close()
        return stopped


@dataclass
class ApiReplica:
    replica_id: str
    port: int
    runtime: ManagedProcess

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"


@dataclass
class PrometheusRuntime:
    container_name: str
    base_url: str
    config_path: Path
    targets_path: Path

    def stop(self) -> None:
        subprocess.run(
            ["docker", "rm", "--force", self.container_name],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )


@dataclass
class StopController:
    event: asyncio.Event = field(default_factory=asyncio.Event)
    reason: str | None = None

    def stop(self, reason: str) -> None:
        if not self.event.is_set():
            self.reason = reason
            self.event.set()


def verify_runtime_identity(config: S3RuntimeConfig) -> dict[str, Any]:
    config.assert_frozen()
    registry = _read_json(config.registry_path, "s3_registry")
    if registry.get("schema_version") != "evm.s3_capacity_registry.v1":
        raise S3RuntimeError("s3_registry_schema_invalid")
    if registry.get("experiment_config_sha256") != config.preparation_config_sha256:
        raise S3RuntimeError("s3_preparation_config_registry_mismatch")
    if registry.get("dataset_version") != config.dataset_version:
        raise S3RuntimeError("s3_dataset_version_mismatch")
    split_manifest_path = config.replay_features_path.parents[2] / "split-manifest.json"
    if file_sha256(split_manifest_path) != registry.get("split_manifest_sha256"):
        raise S3RuntimeError("s3_split_manifest_registry_mismatch")
    split_manifest = _read_json(split_manifest_path, "s3_split_manifest")
    replay = dict(dict(split_manifest.get("samples", {})).get("replay", {}))
    if file_sha256(config.replay_features_path) != replay.get("features_sha256"):
        raise S3RuntimeError("s3_replay_features_digest_mismatch")
    features = np.load(config.replay_features_path, mmap_mode="r", allow_pickle=False)
    if features.ndim != 2 or features.shape[1] != 28 or not features.shape[0]:
        raise S3RuntimeError("s3_replay_features_shape_invalid")
    return {
        "runtime_config_sha256": config.sha256,
        "preparation_config_sha256": config.preparation_config_sha256,
        "registry_sha256": file_sha256(config.registry_path),
        "split_manifest_sha256": file_sha256(split_manifest_path),
        "replay_features_sha256": file_sha256(config.replay_features_path),
        "dataset_identity_sha256": str(registry["dataset_identity_sha256"]),
        "dataset_version": config.dataset_version,
        "replay_rows": int(features.shape[0]),
        "feature_count": int(features.shape[1]),
    }


def available_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def start_managed_process(
    *,
    command: Sequence[str],
    cwd: Path,
    environment: Mapping[str, str],
    private_root: Path,
    label: str,
) -> ManagedProcess:
    stdout_path = private_root / f"{label}.stdout.log"
    stderr_path = private_root / f"{label}.stderr.log"
    stdout_path.parent.mkdir(parents=True, exist_ok=True)
    stdout_stream = stdout_path.open("w", encoding="utf-8", newline="\n")
    stderr_stream = stderr_path.open("w", encoding="utf-8", newline="\n")
    creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    process = subprocess.Popen(
        list(command),
        cwd=cwd,
        env=dict(environment),
        stdout=stdout_stream,
        stderr=stderr_stream,
        text=True,
        creationflags=creationflags,
    )
    return ManagedProcess(
        process=process,
        label=label,
        stdout_path=stdout_path,
        stderr_path=stderr_path,
        stdout_stream=stdout_stream,
        stderr_stream=stderr_stream,
    )


def start_api_replicas(
    *,
    root: Path,
    data_root: Path,
    private_root: Path,
    config: S3RuntimeConfig,
    point: S3LoadPoint,
    source_revision: str,
    source_branch: str,
    marker: str,
) -> list[ApiReplica]:
    replicas: list[ApiReplica] = []
    for index in range(point.api_replicas):
        replica_id = f"replica-{index}"
        port = available_port()
        runtime_root = private_root / replica_id
        runtime_root.mkdir(parents=True)
        environment = dict(os.environ)
        environment.update(
            {
                "PYTHONPATH": str(root / "src"),
                "APP_NAME": "evm-s3-capacity-api",
                "EVM_CONTROL_PLANE_STORE_MODE": "file",
                "EVM_CONTROL_PANEL_LEDGER_ROOT": str(runtime_root / "ledger"),
                "EVM_HOST_DATA_ROOT": str(data_root),
                "MODEL_REGISTRY_PATH": str(runtime_root / "no-model-registry.json"),
                "EVM_S3_CAPACITY_REGISTRY_PATH": str(config.registry_path),
                "EVM_S3_CAPACITY_CPU_WORKERS": str(point.cpu_workers),
                "EVM_S3_CAPACITY_MAX_OUTSTANDING": str(
                    config.max_outstanding_per_replica
                ),
                "EVM_S3_CAPACITY_MAX_OUTSTANDING_BYTES": str(
                    config.max_outstanding_bytes_per_replica
                ),
                "EVM_S3_CAPACITY_MAX_REQUEST_BYTES": str(config.max_request_bytes),
                "EVM_S3_CAPACITY_ADMISSION_WAIT_SECONDS": str(
                    config.admission_wait_seconds
                ),
                "EVM_S3_CAPACITY_REQUEST_TIMEOUT_SECONDS": str(
                    config.request_timeout_seconds
                ),
                "EVM_S3_CAPACITY_RETRY_AFTER_SECONDS": str(
                    config.retry_after_seconds
                ),
                "EVM_S3_CAPACITY_REPLICA_ID": replica_id,
                "EVM_GIT_COMMIT": source_revision,
                "EVM_GIT_BRANCH": source_branch,
                "GIT_COMMIT": source_revision,
                "GIT_BRANCH": source_branch,
                "EVM_OTEL_ENABLED": "true",
                "EVM_OTEL_REQUIRED": "true",
                "EVM_OTEL_PROCESSOR": "batch",
                "OTEL_SERVICE_NAMESPACE": "enterprise-mlops-s3",
                "OTEL_SERVICE_INSTANCE_ID": f"{marker}-{replica_id}",
                "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT": (
                    "http://127.0.0.1:4318/v1/traces"
                ),
            }
        )
        runtime = start_managed_process(
            command=[
                sys.executable,
                "-m",
                "uvicorn",
                "apps.api.main:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
                "--workers",
                "1",
                "--log-level",
                "warning",
                "--header",
                f"x-evm-s3-runtime-marker:{marker}",
            ],
            cwd=root,
            environment=environment,
            private_root=private_root,
            label=f"api-{replica_id}",
        )
        replica = ApiReplica(replica_id=replica_id, port=port, runtime=runtime)
        wait_for_http(f"{replica.base_url}/health", runtime)
        replicas.append(replica)
    return replicas


def wait_for_http(url: str, process: ManagedProcess, *, timeout: float = 30) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.process.poll() is not None:
            raise S3RuntimeError(f"{process.label}_exited:{process.stderr_path.name}")
        try:
            response = requests.get(url, timeout=1)
            if response.status_code == 200:
                return
        except requests.RequestException:
            pass
        time.sleep(0.2)
    raise S3RuntimeError(f"{process.label}_start_timeout")


def terminate_process_tree(pid: int, *, force: bool = False) -> list[int]:
    try:
        parent = psutil.Process(pid)
    except psutil.NoSuchProcess:
        return []
    processes = [*parent.children(recursive=True), parent]
    identities = [process.pid for process in processes]
    for process in reversed(processes):
        try:
            process.kill() if force else process.terminate()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    _, alive = psutil.wait_procs(processes, timeout=10)
    for process in alive:
        try:
            process.kill()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    psutil.wait_procs(alive, timeout=5)
    return identities


def marker_processes(marker: str) -> list[int]:
    found: list[int] = []
    for process in psutil.process_iter(["pid", "cmdline"]):
        try:
            command = " ".join(process.info.get("cmdline") or [])
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
        if marker in command:
            found.append(int(process.info["pid"]))
    return found


def process_tree_sample(pid: int) -> dict[str, float | int]:
    try:
        parent = psutil.Process(pid)
        processes = [parent, *parent.children(recursive=True)]
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return {
            "rss_bytes": 0,
            "cpu_percent": 0.0,
            "cpu_time_seconds": 0.0,
            "process_count": 0,
            "open_handles": 0,
        }
    rss = 0
    cpu = 0.0
    cpu_time = 0.0
    count = 0
    open_handles = 0
    for process in processes:
        try:
            rss += int(process.memory_info().rss)
            cpu += float(process.cpu_percent(None))
            times = process.cpu_times()
            cpu_time += float(times.user + times.system)
            open_handles += process_open_handles(process)
            count += 1
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return {
        "rss_bytes": rss,
        "cpu_percent": cpu,
        "cpu_time_seconds": cpu_time,
        "process_count": count,
        "open_handles": open_handles,
    }


def process_open_handles(process: psutil.Process) -> int:
    try:
        if hasattr(process, "num_handles"):
            return int(process.num_handles())
        if hasattr(process, "num_fds"):
            return int(process.num_fds())
    except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
        return 0
    return 0


def directory_bytes(path: Path) -> int:
    total = 0
    for candidate in path.rglob("*"):
        try:
            if candidate.is_file():
                total += candidate.stat().st_size
        except OSError:
            continue
    return total


def capacity_runtime_gauges(
    replicas: Sequence[ApiReplica],
    *,
    timeout_seconds: float = 1.0,
) -> dict[str, float]:
    names = {
        "evm_s3_capacity_executor_queue_depth",
        "evm_s3_capacity_executor_queue_bytes",
        "evm_s3_capacity_executor_in_flight",
        "evm_s3_capacity_executor_in_flight_bytes",
        "evm_s3_capacity_executor_outstanding",
        "evm_s3_capacity_executor_outstanding_bytes",
        "evm_control_plane_db_pool_size",
        "evm_control_plane_db_pool_available",
        "evm_control_plane_db_pool_in_use",
        "evm_control_plane_db_pool_waiting",
    }
    observed = {name: 0.0 for name in names}
    for replica in replicas:
        response = requests.get(
            f"{replica.base_url}/metrics",
            timeout=timeout_seconds,
        )
        response.raise_for_status()
        for family in text_string_to_metric_families(response.text):
            for sample in family.samples:
                if sample.name in observed:
                    observed[sample.name] += float(sample.value)
    return observed


def start_isolated_prometheus(
    *,
    private_root: Path,
    marker: str,
    replicas: Sequence[ApiReplica],
    scrape_interval_seconds: float,
) -> PrometheusRuntime:
    port = available_port()
    config_path = private_root / "prometheus.yml"
    targets_path = private_root / "prometheus-targets.json"
    config_path.write_text(
        "\n".join(
            [
                "global:",
                f"  scrape_interval: {scrape_interval_seconds:g}s",
                "  evaluation_interval: 1s",
                "scrape_configs:",
                '  - job_name: "s3-isolated"',
                "    file_sd_configs:",
                "      - files:",
                "          - /etc/prometheus/targets/prometheus-targets.json",
                "        refresh_interval: 1s",
                "",
            ]
        ),
        encoding="utf-8",
        newline="\n",
    )
    targets = [
        {
            "targets": [f"host.docker.internal:{replica.port}"],
            "labels": {"replica": replica.replica_id},
        }
        for replica in replicas
    ]
    targets_path.write_text(
        json.dumps(targets, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    container_name = f"evm-s3-prom-{marker}"[:63]
    command = [
        "docker",
        "run",
        "--detach",
        "--rm",
        "--name",
        container_name,
        "--add-host",
        "host.docker.internal:host-gateway",
        "--publish",
        f"127.0.0.1:{port}:9090",
        "--volume",
        f"{config_path}:/etc/prometheus/prometheus.yml:ro",
        "--volume",
        f"{targets_path.parent}:/etc/prometheus/targets:ro",
        "prom/prometheus:v2.55.1",
        "--config.file=/etc/prometheus/prometheus.yml",
        "--storage.tsdb.path=/prometheus",
        "--storage.tsdb.retention.time=1h",
    ]
    result = subprocess.run(command, capture_output=True, text=True, timeout=30)
    if result.returncode != 0:
        raise S3RuntimeError(f"s3_prometheus_start_failed:{result.stderr.strip()}")
    runtime = PrometheusRuntime(
        container_name=container_name,
        base_url=f"http://127.0.0.1:{port}",
        config_path=config_path,
        targets_path=targets_path,
    )
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        try:
            if requests.get(f"{runtime.base_url}/-/ready", timeout=1).status_code == 200:
                wait_prometheus_targets(runtime, replicas)
                return runtime
        except requests.RequestException:
            pass
        time.sleep(0.25)
    runtime.stop()
    raise S3RuntimeError("s3_prometheus_ready_timeout")


def prometheus_query(runtime: PrometheusRuntime, query: str) -> list[dict[str, Any]]:
    response = requests.get(
        f"{runtime.base_url}/api/v1/query",
        params={"query": query},
        timeout=10,
    )
    response.raise_for_status()
    payload = response.json()
    if payload.get("status") != "success":
        raise S3RuntimeError(f"s3_prometheus_query_failed:{query}")
    return list(payload.get("data", {}).get("result", []))


def wait_prometheus_targets(
    runtime: PrometheusRuntime,
    replicas: Sequence[ApiReplica],
    *,
    timeout: float = 20,
) -> dict[str, int]:
    expected = {replica.replica_id for replica in replicas}
    deadline = time.monotonic() + timeout
    last: dict[str, int] = {}
    while time.monotonic() < deadline:
        result = prometheus_query(runtime, 'up{job="s3-isolated"}')
        last = {
            str(item.get("metric", {}).get("replica")): int(float(item["value"][1]))
            for item in result
        }
        if set(last) == expected and all(last.values()):
            return last
        time.sleep(0.5)
    raise S3RuntimeError(f"s3_prometheus_targets_not_up:{last}")


def _section(payload: Mapping[str, Any], name: str) -> dict[str, Any]:
    value = payload.get(name)
    if not isinstance(value, dict):
        raise S3RuntimeError(f"s3_runtime_section_missing:{name}")
    return dict(value)


def _sha256(value: Any, name: str) -> str:
    text = str(value or "")
    if len(text) != 64 or any(character not in "0123456789abcdef" for character in text):
        raise S3RuntimeError(f"s3_runtime_sha_invalid:{name}")
    return text


def _read_json(path: Path, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise S3RuntimeError(f"{label}_invalid:{path.name}") from exc
    if not isinstance(payload, dict):
        raise S3RuntimeError(f"{label}_not_object")
    return payload


def utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def canonical_write(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_bytes(
        (json.dumps(payload, sort_keys=True, indent=2) + "\n").encode("utf-8")
    )
    temporary.replace(path)


def canonical_digest(payload: object) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


class ReplayPayloadFactory:
    def __init__(
        self,
        *,
        features_path: Path,
        dataset_identity_sha256: str,
        family: str,
        seed: int,
        cache_size: int = 32768,
    ):
        if family not in PROBE_FAMILIES:
            raise S3RuntimeError(f"s3_probe_family_invalid:{family}")
        features = np.load(features_path, mmap_mode="r", allow_pickle=False)
        if features.ndim != 2 or features.shape[1] != 28:
            raise S3RuntimeError("s3_replay_features_shape_invalid")
        rng = np.random.default_rng(seed)
        count = min(cache_size, int(features.shape[0]))
        indices = rng.choice(int(features.shape[0]), size=count, replace=False)
        self._bodies = [
            json.dumps(
                {
                    "schema_version": "evm.s3_capacity_probe_request.v1",
                    "probe_family": family,
                    "dataset_identity_sha256": dataset_identity_sha256,
                    "features": features[int(index)].astype(float).tolist(),
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
            for index in indices
        ]
        self.sequence_sha256 = canonical_digest([int(index) for index in indices])
        self.cache_size = len(self._bodies)

    def body(self, index: int) -> bytes:
        return self._bodies[index % len(self._bodies)]


def deterministic_traceparent(run_id: str, request_index: int) -> tuple[str, str, bool]:
    trace_id = hashlib.sha256(
        f"{run_id}:trace:{request_index}".encode("utf-8")
    ).hexdigest()[:32]
    span_id = hashlib.sha256(
        f"{run_id}:span:{request_index}".encode("utf-8")
    ).hexdigest()[:16]
    sampled = request_index % 100 == 0
    flags = "01" if sampled else "00"
    return f"00-{trace_id}-{span_id}-{flags}", trace_id, sampled


async def run_load_phase(
    *,
    point: S3LoadPoint,
    replicas: Sequence[ApiReplica],
    payloads: ReplayPayloadFactory,
    run_id: str,
    duration_seconds: float,
    client_max_in_flight: int,
    request_timeout_seconds: float,
    stop: StopController,
    capture: bool,
) -> dict[str, Any]:
    limits = httpx.Limits(
        max_connections=client_max_in_flight,
        max_keepalive_connections=min(client_max_in_flight, 256),
        keepalive_expiry=30,
    )
    timeout = httpx.Timeout(request_timeout_seconds + 2)
    observations: list[dict[str, Any]] = []
    expected_sampled_trace_contracts: dict[str, str] = {}
    started_monotonic = time.perf_counter()
    started_utc = utc_now()
    request_index = 0
    planned_request_count: int | None = None
    request_index_lock = asyncio.Lock()

    async def next_index() -> int:
        nonlocal request_index
        async with request_index_lock:
            value = request_index
            request_index += 1
            return value

    def record_observation(observation: Mapping[str, Any]) -> None:
        if not capture:
            return
        observations.append(dict(observation))
        if not observation.get("trace_sampled"):
            return
        status_code = int(observation.get("status_code", 0))
        contract = (
            "full"
            if status_code == 200
            else "admission"
            if status_code > 0
            else "client_only"
        )
        expected_sampled_trace_contracts[str(observation["trace_id"])] = contract

    async with httpx.AsyncClient(limits=limits, timeout=timeout) as client:
        if point.mode == "closed":
            concurrency = int(point.load)

            async def closed_worker(worker_index: int) -> None:
                while (
                    time.perf_counter() - started_monotonic < duration_seconds
                    and not stop.event.is_set()
                ):
                    index = await next_index()
                    observation = await _send_capacity_request(
                        client=client,
                        base_url=replicas[index % len(replicas)].base_url,
                        body=payloads.body(index),
                        run_id=run_id,
                        request_index=index,
                        phase_started=started_monotonic,
                        scheduled_at=None,
                        load_generator_permit_wait_seconds=0,
                    )
                    record_observation(observation)

            await asyncio.gather(
                *(closed_worker(index) for index in range(concurrency))
            )
        elif point.mode == "open":
            rate = float(point.load)
            planned = max(1, int(math.floor(rate * duration_seconds)))
            planned_request_count = planned
            phase_deadline = started_monotonic + duration_seconds
            tasks: set[asyncio.Task[dict[str, Any]]] = set()

            async def record_completed(
                completed: set[asyncio.Task[dict[str, Any]]],
            ) -> None:
                for task in completed:
                    observation = task.result()
                    record_observation(observation)

            for index in range(planned):
                if stop.event.is_set():
                    break
                target = started_monotonic + index / rate
                delay = target - time.perf_counter()
                if delay > 0:
                    await asyncio.sleep(delay)
                if time.perf_counter() >= phase_deadline:
                    break
                permit_started = time.perf_counter()
                while len(tasks) >= client_max_in_flight:
                    completed, tasks = await asyncio.wait(
                        tasks,
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    await record_completed(completed)
                    if time.perf_counter() >= phase_deadline:
                        break
                if time.perf_counter() >= phase_deadline:
                    break
                permit_wait = time.perf_counter() - permit_started
                task = asyncio.create_task(
                    _send_capacity_request(
                        client=client,
                        base_url=replicas[index % len(replicas)].base_url,
                        body=payloads.body(index),
                        run_id=run_id,
                        request_index=index,
                        phase_started=started_monotonic,
                        scheduled_at=target,
                        load_generator_permit_wait_seconds=permit_wait,
                    )
                )
                tasks.add(task)
                request_index += 1
            while tasks:
                completed, tasks = await asyncio.wait(
                    tasks,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                await record_completed(completed)
        else:
            raise S3RuntimeError(f"s3_load_mode_invalid:{point.mode}")

    finished_monotonic = time.perf_counter()
    return {
        "started_at": started_utc,
        "finished_at": utc_now(),
        "declared_duration_seconds": duration_seconds,
        "observed_elapsed_seconds": finished_monotonic - started_monotonic,
        "request_count": request_index,
        "planned_request_count": planned_request_count,
        "unscheduled_request_count": (
            max(0, planned_request_count - request_index)
            if planned_request_count is not None
            else 0
        ),
        "observations": observations,
        "expected_sampled_trace_ids": sorted(expected_sampled_trace_contracts),
        "expected_sampled_trace_contracts": dict(
            sorted(expected_sampled_trace_contracts.items())
        ),
        "stopped": stop.event.is_set(),
        "stop_reason": stop.reason,
    }


async def _send_capacity_request(
    *,
    client: httpx.AsyncClient,
    base_url: str,
    body: bytes,
    run_id: str,
    request_index: int,
    phase_started: float,
    scheduled_at: float | None,
    load_generator_permit_wait_seconds: float,
) -> dict[str, Any]:
    traceparent, trace_id, sampled = deterministic_traceparent(run_id, request_index)
    request_started = time.perf_counter()
    start_lag = (
        max(0.0, request_started - scheduled_at) if scheduled_at is not None else 0
    )
    status = 0
    headers: Mapping[str, str] = {}
    response_payload: dict[str, Any] = {}
    transport_error: str | None = None
    try:
        response = await client.post(
            f"{base_url}/control-panel/v1/scenario-workloads/capacity-probes/predict",
            content=body,
            headers={
                "Content-Type": "application/json",
                "traceparent": traceparent,
                "x-evm-s3-run-id": run_id,
            },
        )
        status = response.status_code
        headers = response.headers
        try:
            parsed = response.json()
            if isinstance(parsed, dict):
                response_payload = parsed
        except ValueError:
            response_payload = {}
    except httpx.HTTPError as exc:
        transport_error = type(exc).__name__
    completed = time.perf_counter()
    timings = response_payload.get("timings")
    runtime = response_payload.get("runtime")
    detail = response_payload.get("detail")
    return {
        "request_index": request_index,
        "status_code": status,
        "transport_error": transport_error,
        "started_offset_seconds": request_started - phase_started,
        "completed_offset_seconds": completed - phase_started,
        "latency_ms": (completed - request_started) * 1000,
        "start_lag_ms": start_lag * 1000,
        "load_generator_permit_wait_ms": (
            load_generator_permit_wait_seconds * 1000
        ),
        "retry_after": headers.get("Retry-After"),
        "trace_id": trace_id,
        "trace_sampled": sampled,
        "response_trace_id": headers.get("x-evm-trace-id"),
        "trace_identity_matches": headers.get("x-evm-trace-id") == trace_id,
        "server_timings": timings if isinstance(timings, dict) else {},
        "runtime": runtime if isinstance(runtime, dict) else {},
        "error_code": (
            detail.get("error") if isinstance(detail, dict) else None
        ),
    }


async def collect_resource_samples(
    *,
    replicas: Sequence[ApiReplica],
    interval_seconds: float,
    maximum_host_cpu_percent: float,
    maximum_process_tree_rss_bytes: int,
    stop: StopController,
    finished: asyncio.Event,
    artifact_root: Path,
    runtime_metrics_timeout_seconds: float = 1.0,
    runtime_metrics_max_consecutive_failures: int = 3,
) -> list[dict[str, Any]]:
    samples: list[dict[str, Any]] = []
    runner = psutil.Process(os.getpid())
    previous_api_cpu = {
        replica.replica_id: float(
            process_tree_sample(replica.runtime.pid)["cpu_time_seconds"]
        )
        for replica in replicas
    }
    runner_times = runner.cpu_times()
    previous_runner_cpu = float(runner_times.user + runner_times.system)
    psutil.cpu_percent(None)
    high_cpu_samples = 0
    consecutive_runtime_metric_failures = 0
    started = time.perf_counter()
    previous_sample = started
    while not finished.is_set():
        try:
            await asyncio.wait_for(finished.wait(), timeout=interval_seconds)
            break
        except TimeoutError:
            pass
        sampled_at = time.perf_counter()
        elapsed = max(0.000001, sampled_at - previous_sample)
        api = {
            replica.replica_id: process_tree_sample(replica.runtime.pid)
            for replica in replicas
        }
        for replica_id, observed in api.items():
            cpu_time = float(observed["cpu_time_seconds"])
            observed["cpu_percent"] = max(
                0.0,
                (cpu_time - previous_api_cpu.get(replica_id, cpu_time))
                / elapsed
                * 100,
            )
            previous_api_cpu[replica_id] = cpu_time
        runner_times = runner.cpu_times()
        runner_cpu = float(runner_times.user + runner_times.system)
        host_cpu = float(psutil.cpu_percent(None))
        runtime_gauges: dict[str, float] = {}
        runtime_gauge_error: str | None = None
        try:
            runtime_gauges = await asyncio.to_thread(
                capacity_runtime_gauges,
                replicas,
                timeout_seconds=runtime_metrics_timeout_seconds,
            )
            consecutive_runtime_metric_failures = 0
        except requests.RequestException as exc:
            consecutive_runtime_metric_failures += 1
            runtime_gauge_error = type(exc).__name__
        artifact_bytes = await asyncio.to_thread(directory_bytes, artifact_root)
        sample = {
            "offset_seconds": sampled_at - started,
            "host_cpu_percent": host_cpu,
            "host_memory_percent": float(psutil.virtual_memory().percent),
            "api": api,
            "api_process_tree_rss_bytes": sum(
                int(value["rss_bytes"]) for value in api.values()
            ),
            "api_process_tree_cpu_percent": sum(
                float(value["cpu_percent"]) for value in api.values()
            ),
            "api_process_tree_open_handles": sum(
                int(value["open_handles"]) for value in api.values()
            ),
            "load_generator_rss_bytes": int(runner.memory_info().rss),
            "load_generator_open_handles": process_open_handles(runner),
            "load_generator_cpu_percent": max(
                0.0,
                (runner_cpu - previous_runner_cpu) / elapsed * 100,
            ),
            "artifact_bytes": artifact_bytes,
            "runtime_gauge_sample_ok": runtime_gauge_error is None,
            "runtime_gauge_error": runtime_gauge_error,
            "runtime_gauge_consecutive_failures": (
                consecutive_runtime_metric_failures
            ),
            **runtime_gauges,
        }
        previous_runner_cpu = runner_cpu
        previous_sample = sampled_at
        samples.append(sample)
        if any(not psutil.pid_exists(replica.runtime.pid) for replica in replicas):
            stop.stop("api_process_exited")
        high_cpu_samples = high_cpu_samples + 1 if host_cpu > maximum_host_cpu_percent else 0
        if high_cpu_samples >= 3:
            stop.stop("host_cpu_guardrail")
        if sample["api_process_tree_rss_bytes"] > maximum_process_tree_rss_bytes:
            stop.stop("api_process_tree_rss_guardrail")
        if (
            consecutive_runtime_metric_failures
            >= runtime_metrics_max_consecutive_failures
        ):
            stop.stop("runtime_metrics_unavailable")
            break
    return samples


def summarize_load_phase(phase: Mapping[str, Any]) -> dict[str, Any]:
    observations = [dict(item) for item in phase.get("observations", [])]
    statuses = Counter(int(item.get("status_code", 0)) for item in observations)
    duration = float(phase["declared_duration_seconds"])
    successful = [item for item in observations if int(item.get("status_code", 0)) == 200]
    completed_in_window = [
        item
        for item in successful
        if float(item.get("completed_offset_seconds", math.inf)) <= duration
    ]
    latencies = [float(item["latency_ms"]) for item in successful]
    all_latencies = [float(item["latency_ms"]) for item in observations]
    server_fields = (
        "admission_wait_ms",
        "queue_wait_ms",
        "validation_ms",
        "transform_ms",
        "prediction_ms",
        "compute_ms",
        "total_ms",
    )
    server_timings = {
        field: _statistics(
            [
                float(dict(item.get("server_timings", {})).get(field, 0))
                for item in successful
            ]
        )
        for field in server_fields
    }
    replica_counts = Counter(
        str(dict(item.get("runtime", {})).get("api_replica_id", "missing"))
        for item in successful
    )
    worker_counts = Counter(
        (
            str(dict(item.get("runtime", {})).get("api_replica_id", "missing")),
            str(dict(item.get("runtime", {})).get("worker_slot", "missing")),
        )
        for item in successful
    )
    server_responses = [
        item for item in observations if int(item.get("status_code", 0)) > 0
    ]
    response_trace_count = sum(
        bool(item.get("response_trace_id")) for item in server_responses
    )
    trace_matches = sum(
        bool(item.get("trace_identity_matches")) for item in server_responses
    )
    client_request_identity_count = sum(
        isinstance(item.get("request_index"), int)
        and bool(item.get("trace_id"))
        for item in observations
    )
    return {
        "declared_duration_seconds": duration,
        "observed_elapsed_seconds": float(phase["observed_elapsed_seconds"]),
        "request_count": len(observations),
        "planned_request_count": phase.get("planned_request_count"),
        "unscheduled_request_count": int(
            phase.get("unscheduled_request_count", 0)
        ),
        "status_counts": {str(key): value for key, value in sorted(statuses.items())},
        "successful_count": len(successful),
        "successful_within_window": len(completed_in_window),
        "service_rate_per_second": len(completed_in_window) / duration,
        "offered_rate_per_second": len(observations) / duration,
        "target_arrival_rate_per_second": (
            int(phase["planned_request_count"]) / duration
            if phase.get("planned_request_count") is not None
            else None
        ),
        "error_rate": (
            (len(observations) - len(successful)) / len(observations)
            if observations
            else 1.0
        ),
        "latency_ms": _statistics(latencies),
        "all_response_latency_ms": _statistics(all_latencies),
        "server_timings_ms": server_timings,
        "start_lag_ms": _statistics(
            [float(item.get("start_lag_ms", 0)) for item in observations]
        ),
        "load_generator_permit_wait_ms": _statistics(
            [
                float(item.get("load_generator_permit_wait_ms", 0))
                for item in observations
            ]
        ),
        "response_trace_id_count": response_trace_count,
        "trace_identity_match_count": trace_matches,
        "server_response_count": len(server_responses),
        "client_request_identity_count": client_request_identity_count,
        "replica_request_counts": dict(replica_counts),
        "worker_request_counts": {
            f"{replica}/slot-{slot}": count
            for (replica, slot), count in worker_counts.items()
        },
        "transport_error_count": sum(
            bool(item.get("transport_error")) for item in observations
        ),
        "stopped": bool(phase.get("stopped")),
        "stop_reason": phase.get("stop_reason"),
    }


def resource_summary(samples: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    fields = (
        "host_cpu_percent",
        "host_memory_percent",
        "api_process_tree_rss_bytes",
        "api_process_tree_cpu_percent",
        "api_process_tree_open_handles",
        "load_generator_rss_bytes",
        "load_generator_cpu_percent",
        "load_generator_open_handles",
        "artifact_bytes",
        "evm_s3_capacity_executor_queue_depth",
        "evm_s3_capacity_executor_queue_bytes",
        "evm_s3_capacity_executor_in_flight",
        "evm_s3_capacity_executor_in_flight_bytes",
        "evm_s3_capacity_executor_outstanding",
        "evm_s3_capacity_executor_outstanding_bytes",
        "evm_control_plane_db_pool_size",
        "evm_control_plane_db_pool_available",
        "evm_control_plane_db_pool_in_use",
        "evm_control_plane_db_pool_waiting",
    )
    return {
        field: _statistics(
            [float(sample[field]) for sample in samples if field in sample]
        )
        for field in fields
    }


def _statistics(values: Sequence[float]) -> dict[str, float | int]:
    observed = [float(value) for value in values]
    if any(not math.isfinite(value) for value in observed):
        raise S3RuntimeError("s3_non_finite_metric")
    finite = sorted(observed)
    if not finite:
        return {"count": 0, "min": 0.0, "p50": 0.0, "p95": 0.0, "p99": 0.0, "max": 0.0, "mean": 0.0}

    def percentile(fraction: float) -> float:
        rank = max(0, min(len(finite) - 1, math.ceil(fraction * len(finite)) - 1))
        return finite[rank]

    return {
        "count": len(finite),
        "min": finite[0],
        "p50": percentile(0.50),
        "p95": percentile(0.95),
        "p99": percentile(0.99),
        "max": finite[-1],
        "mean": sum(finite) / len(finite),
    }


def source_identity(root: Path) -> tuple[str, str]:
    revision = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=15,
        check=True,
    ).stdout.strip()
    branch = subprocess.run(
        ["git", "branch", "--show-current"],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=15,
        check=True,
    ).stdout.strip()
    if not branch:
        candidates = subprocess.run(
            ["git", "branch", "--remotes", "--contains", revision],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=15,
            check=True,
        ).stdout.splitlines()
        preferred = next(
            (
                value.strip().removeprefix("origin/")
                for value in candidates
                if "origin/codex/distributed-scale-validation-plan" in value
            ),
            "detached-head",
        )
        branch = preferred
    return revision, branch


def runtime_catalog_preflight(
    replicas: Sequence[ApiReplica],
    identity: Mapping[str, Any],
) -> dict[str, Any]:
    expected_families = set(PROBE_FAMILIES)
    results: dict[str, Any] = {}
    for replica in replicas:
        response = requests.get(
            f"{replica.base_url}/control-panel/v1/scenario-workloads/capacity-probes",
            timeout=10,
        )
        response.raise_for_status()
        payload = response.json()
        observed_families = {
            str(item.get("probe_family")) for item in payload.get("probes", [])
        }
        checks = {
            "dataset_version": (
                payload.get("dataset_version") == identity["dataset_version"]
            ),
            "dataset_identity": (
                payload.get("dataset_identity_sha256")
                == identity["dataset_identity_sha256"]
            ),
            "split_manifest": (
                payload.get("split_manifest_sha256")
                == identity["split_manifest_sha256"]
            ),
            "probe_families": observed_families == expected_families,
        }
        if not all(checks.values()):
            raise S3RuntimeError(
                f"s3_catalog_identity_preflight_failed:{replica.replica_id}:{checks}"
            )
        results[replica.replica_id] = checks
    return results


def prometheus_scalar(runtime: PrometheusRuntime, query: str) -> float:
    result = prometheus_query(runtime, query)
    if not result:
        return 0.0
    return sum(float(item["value"][1]) for item in result)


def prometheus_capacity_snapshot(
    runtime: PrometheusRuntime,
    *,
    lookback_seconds: float,
) -> dict[str, Any]:
    lookback = max(1, int(math.ceil(lookback_seconds)))
    gauges = {
        metric: prometheus_scalar(runtime, f"sum({metric})")
        for metric in TERMINAL_GAUGES
    }
    peaks = {
        metric: prometheus_scalar(
            runtime,
            f"max(max_over_time({metric}[{lookback}s]))",
        )
        for metric in TERMINAL_GAUGES
    }
    counters = {
        "request_ok": prometheus_scalar(
            runtime,
            'sum(evm_s3_capacity_probe_requests_total{outcome="ok"})',
        ),
        "request_error": prometheus_scalar(
            runtime,
            'sum(evm_s3_capacity_probe_requests_total{outcome!="ok"})',
        ),
        "admission_accepted": prometheus_scalar(
            runtime,
            'sum(evm_s3_capacity_executor_admission_total{outcome="accepted"})',
        ),
        "admission_rejected": prometheus_scalar(
            runtime,
            'sum(evm_s3_capacity_executor_admission_total{outcome="rejected"})',
        ),
        "worker_ok": prometheus_scalar(
            runtime,
            'sum(evm_s3_capacity_executor_worker_tasks_total{outcome="ok"})',
        ),
        "worker_error": prometheus_scalar(
            runtime,
            'sum(evm_s3_capacity_executor_worker_tasks_total{outcome!="ok"})',
        ),
    }
    wait_histograms = {}
    for name in (
        "evm_s3_capacity_executor_admission_wait_seconds",
        "evm_s3_capacity_executor_queue_wait_seconds",
    ):
        count = prometheus_scalar(runtime, f"sum({name}_count)")
        total = prometheus_scalar(runtime, f"sum({name}_sum)")
        wait_histograms[name] = {
            "count": count,
            "sum_seconds": total,
            "mean_seconds": total / count if count else 0.0,
        }
    return {
        "targets": prometheus_query(runtime, 'up{job="s3-isolated"}'),
        "terminal_gauges": gauges,
        "observed_peaks": peaks,
        "counters": counters,
        "wait_histograms": wait_histograms,
    }


def prometheus_measurement_delta(
    baseline: Mapping[str, Any],
    terminal: Mapping[str, Any],
) -> dict[str, Any]:
    before_counters = dict(baseline.get("counters", {}))
    after_counters = dict(terminal.get("counters", {}))
    counters = {
        key: max(0.0, float(after_counters.get(key, 0)) - float(value))
        for key, value in before_counters.items()
    }
    waits: dict[str, Any] = {}
    before_waits = dict(baseline.get("wait_histograms", {}))
    after_waits = dict(terminal.get("wait_histograms", {}))
    for name, before in before_waits.items():
        after = dict(after_waits.get(name, {}))
        before = dict(before)
        count = max(
            0.0,
            float(after.get("count", 0)) - float(before.get("count", 0)),
        )
        total = max(
            0.0,
            float(after.get("sum_seconds", 0))
            - float(before.get("sum_seconds", 0)),
        )
        waits[name] = {
            "count": count,
            "sum_seconds": total,
            "mean_seconds": total / count if count else 0.0,
        }
    return {"counters": counters, "wait_histograms": waits}


def wait_capacity_drain(
    runtime: PrometheusRuntime,
    *,
    timeout_seconds: float,
) -> dict[str, float]:
    deadline = time.monotonic() + timeout_seconds
    last: dict[str, float] = {}
    while time.monotonic() < deadline:
        last = {
            metric: prometheus_scalar(runtime, f"sum({metric})")
            for metric in TERMINAL_GAUGES
        }
        if all(value == 0 for value in last.values()):
            return last
        time.sleep(0.25)
    raise S3RuntimeError(f"s3_capacity_executor_drain_timeout:{last}")


def otlp_trace_summary(
    path: Path,
    *,
    offset: int,
    expected_trace_ids: Sequence[str] = (),
    expected_trace_contracts: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    contracts = (
        dict(expected_trace_contracts)
        if expected_trace_contracts is not None
        else {trace_id: "full" for trace_id in expected_trace_ids}
    )
    expected = set(contracts)
    by_trace: dict[str, set[str]] = defaultdict(set)
    raw = b""
    if path.is_file():
        with path.open("rb") as stream:
            stream.seek(offset)
            raw = stream.read()
    for line in raw.splitlines():
        try:
            payload = json.loads(line)
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
        for resource in payload.get("resourceSpans", []):
            for scope in resource.get("scopeSpans", []):
                for span in scope.get("spans", []):
                    trace_id = str(span.get("traceId") or "")
                    name = str(span.get("name") or "")
                    if trace_id in expected and name:
                        by_trace[trace_id].add(name)
    required_by_contract = {
        "full": REQUIRED_TRACE_SPANS,
        "admission": REQUIRED_ADMISSION_TRACE_SPANS,
        "client_only": set(),
    }
    invalid_contracts = sorted(set(contracts.values()) - set(required_by_contract))
    if invalid_contracts:
        raise S3RuntimeError(
            f"s3_trace_contract_invalid:{','.join(invalid_contracts)}"
        )
    complete = {
        trace_id
        for trace_id, contract in contracts.items()
        if required_by_contract[contract].issubset(by_trace.get(trace_id, set()))
    }
    server_expected = {
        trace_id
        for trace_id, contract in contracts.items()
        if contract != "client_only"
    }
    contract_counts = Counter(contracts.values())
    complete_contract_counts = Counter(contracts[trace_id] for trace_id in complete)
    return {
        "expected_sampled_trace_count": len(expected),
        "expected_server_sampled_trace_count": len(server_expected),
        "client_only_sampled_trace_count": contract_counts["client_only"],
        "observed_sampled_trace_count": sum(
            trace_id in by_trace for trace_id in server_expected
        ),
        "complete_sampled_trace_count": len(complete),
        "missing_sampled_trace_count": len(expected - complete),
        "expected_trace_contract_counts": dict(sorted(contract_counts.items())),
        "complete_trace_contract_counts": dict(
            sorted(complete_contract_counts.items())
        ),
        "required_span_names": sorted(REQUIRED_TRACE_SPANS),
        "admission_required_span_names": sorted(REQUIRED_ADMISSION_TRACE_SPANS),
        "observed_span_names": sorted(
            {name for names in by_trace.values() for name in names}
        ),
        "raw_tail_sha256": hashlib.sha256(raw).hexdigest(),
        "raw_tail_bytes": len(raw),
    }


def wait_for_otlp_trace_summary(
    path: Path,
    *,
    offset: int,
    timeout_seconds: float,
    poll_interval_seconds: float,
    expected_trace_ids: Sequence[str] = (),
    expected_trace_contracts: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    started = time.monotonic()
    deadline = started + timeout_seconds
    poll_count = 0
    while True:
        poll_count += 1
        summary = otlp_trace_summary(
            path,
            offset=offset,
            expected_trace_ids=expected_trace_ids,
            expected_trace_contracts=expected_trace_contracts,
        )
        if int(summary["missing_sampled_trace_count"]) == 0:
            break
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        time.sleep(min(poll_interval_seconds, remaining))
    summary["flush_wait_seconds"] = max(0.0, time.monotonic() - started)
    summary["flush_poll_count"] = poll_count
    summary["flush_completed"] = (
        int(summary["missing_sampled_trace_count"]) == 0
    )
    return summary


def run_capacity_point(
    *,
    root: Path,
    data_root: Path,
    suite_root: Path,
    config: S3RuntimeConfig,
    point: S3LoadPoint,
    repetition: int,
    source_revision: str,
    source_branch: str,
    trace_path: Path,
) -> dict[str, Any]:
    config.assert_frozen()
    identity = verify_runtime_identity(config)
    run_token = uuid4().hex[:8]
    marker = f"s3-{point.point_id}-{repetition}-{run_token}"[:56]
    private_root = suite_root / point.point_id / f"repetition-{repetition}"
    private_root.mkdir(parents=True, exist_ok=False)
    run_id = f"{marker}-{source_revision[:8]}"
    trace_offset = trace_path.stat().st_size if trace_path.is_file() else 0
    replicas: list[ApiReplica] = []
    prometheus: PrometheusRuntime | None = None
    started_at = utc_now()
    cleanup: dict[str, Any] = {}
    private_payload: dict[str, Any] = {
        "schema_version": "evm.s3_capacity_point_private.v1",
        "started_at": started_at,
        "run_id": run_id,
        "point": point.__dict__,
        "repetition": repetition,
        "source_revision": source_revision,
        "runtime_config_sha256": config.sha256,
    }
    try:
        replicas = start_api_replicas(
            root=root,
            data_root=data_root,
            private_root=private_root,
            config=config,
            point=point,
            source_revision=source_revision,
            source_branch=source_branch,
            marker=marker,
        )
        catalog = runtime_catalog_preflight(replicas, identity)
        prometheus = start_isolated_prometheus(
            private_root=private_root,
            marker=marker,
            replicas=replicas,
            scrape_interval_seconds=config.prometheus_scrape_interval_seconds,
        )
        payloads = ReplayPayloadFactory(
            features_path=config.replay_features_path,
            dataset_identity_sha256=str(identity["dataset_identity_sha256"]),
            family=point.probe_family,
            seed=config.seed,
        )
        warmup_duration = (
            config.closed_warmup_seconds
            if point.mode == "closed"
            else config.open_warmup_seconds
        )
        measurement_duration = (
            config.closed_measurement_seconds
            if point.mode == "closed"
            else config.open_measurement_seconds
        )
        cooldown_duration = (
            config.closed_cooldown_seconds
            if point.mode == "closed"
            else config.open_cooldown_seconds
        )
        asyncio.run(
            run_load_phase(
                point=point,
                replicas=replicas,
                payloads=payloads,
                run_id=f"{run_id}-warmup",
                duration_seconds=warmup_duration,
                client_max_in_flight=config.client_max_in_flight,
                request_timeout_seconds=config.request_timeout_seconds,
                stop=StopController(),
                capture=False,
            )
        )
        time.sleep(config.prometheus_scrape_interval_seconds * 1.25)
        metric_baseline = prometheus_capacity_snapshot(
            prometheus,
            lookback_seconds=warmup_duration + 5,
        )
        stop = StopController()
        phase, samples = asyncio.run(
            _run_measured_phase(
                point=point,
                replicas=replicas,
                payloads=payloads,
                run_id=run_id,
                duration_seconds=measurement_duration,
                config=config,
                stop=stop,
                artifact_root=private_root,
            )
        )
        load = summarize_load_phase(phase)
        resources = resource_summary(samples)
        terminal = wait_capacity_drain(
            prometheus,
            timeout_seconds=config.queue_drain_timeout_seconds,
        )
        time.sleep(config.prometheus_scrape_interval_seconds * 1.25)
        metrics = prometheus_capacity_snapshot(
            prometheus,
            lookback_seconds=(
                warmup_duration + measurement_duration + cooldown_duration + 5
            ),
        )
        targets = wait_prometheus_targets(prometheus, replicas)
        measurement_metric_delta = prometheus_measurement_delta(
            metric_baseline,
            metrics,
        )
        time.sleep(cooldown_duration)
        guardrails = evaluate_point_guardrails(load, resources, config)
        expected_traces = list(phase["expected_sampled_trace_ids"])
        expected_trace_contracts = dict(
            phase["expected_sampled_trace_contracts"]
        )
        private_payload.update(
            {
                "catalog_preflight": catalog,
                "identity": identity,
                "payload_sequence_sha256": payloads.sequence_sha256,
                "payload_cache_size": payloads.cache_size,
                "warmup_seconds": warmup_duration,
                "measurement": phase,
                "resource_samples": samples,
                "metric_baseline": metric_baseline,
                "metrics": metrics,
                "measurement_metric_delta": measurement_metric_delta,
                "terminal_gauges": terminal,
                "prometheus_targets": targets,
                "guardrails": guardrails,
                "expected_sampled_trace_ids": expected_traces,
                "expected_sampled_trace_contracts": expected_trace_contracts,
            }
        )
    except Exception as exc:
        private_payload["failure"] = {
            "type": type(exc).__name__,
            "message": str(exc),
        }
    finally:
        trace_wait = wait_for_otlp_trace_summary(
            trace_path,
            offset=trace_offset,
            expected_trace_ids=private_payload.get(
                "expected_sampled_trace_ids", []
            ),
            expected_trace_contracts=private_payload.get(
                "expected_sampled_trace_contracts"
            ),
            timeout_seconds=config.trace_flush_seconds,
            poll_interval_seconds=config.trace_poll_interval_seconds,
        )
        if prometheus is not None:
            prometheus.stop()
        stopped_pids: list[int] = []
        for replica in reversed(replicas):
            try:
                stopped_pids.extend(replica.runtime.stop())
            except (subprocess.SubprocessError, psutil.Error) as exc:
                cleanup.setdefault("errors", []).append(
                    f"{replica.replica_id}:{type(exc).__name__}"
                )
        trace = otlp_trace_summary(
            trace_path,
            offset=trace_offset,
            expected_trace_ids=private_payload.get(
                "expected_sampled_trace_ids", []
            ),
            expected_trace_contracts=private_payload.get(
                "expected_sampled_trace_contracts"
            ),
        )
        trace.update(
            {
                "flush_wait_seconds": trace_wait["flush_wait_seconds"],
                "flush_poll_count": trace_wait["flush_poll_count"],
                "flush_completed": trace_wait["flush_completed"],
                "flush_boundary": "before_api_process_stop",
            }
        )
        lingering_pids = [pid for pid in stopped_pids if psutil.pid_exists(pid)]
        marker_pids = marker_processes(marker)
        cleanup.update(
            {
                "stopped_pid_count": len(set(stopped_pids)),
                "lingering_pid_count": len(lingering_pids),
                "marker_process_count": len(marker_pids),
                "prometheus_container_absent": not _docker_container_exists(
                    prometheus.container_name if prometheus else ""
                ),
            }
        )
        private_payload["trace"] = trace
        private_payload["cleanup"] = cleanup
        private_payload["finished_at"] = utc_now()

    assertions = evaluate_point_assertions(private_payload)
    private_payload["assertions"] = assertions
    private_payload["evidence_valid"] = all(assertions.values())
    private_path = private_root / "point-evidence-private.json"
    canonical_write(private_path, private_payload)
    public = public_point_projection(private_payload)
    public["private_evidence_sha256"] = file_sha256(private_path)
    public["private_evidence_bytes"] = private_path.stat().st_size
    return public


async def _run_measured_phase(
    *,
    point: S3LoadPoint,
    replicas: Sequence[ApiReplica],
    payloads: ReplayPayloadFactory,
    run_id: str,
    duration_seconds: float,
    config: S3RuntimeConfig,
    stop: StopController,
    artifact_root: Path,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    finished = asyncio.Event()
    sampler = asyncio.create_task(
        collect_resource_samples(
            replicas=replicas,
            interval_seconds=config.resource_sample_interval_seconds,
            maximum_host_cpu_percent=config.maximum_host_cpu_percent,
            maximum_process_tree_rss_bytes=config.maximum_process_tree_rss_bytes,
            stop=stop,
            finished=finished,
            artifact_root=artifact_root,
            runtime_metrics_timeout_seconds=(
                config.resource_metrics_timeout_seconds
            ),
            runtime_metrics_max_consecutive_failures=(
                config.resource_metrics_max_consecutive_failures
            ),
        )
    )
    try:
        phase = await run_load_phase(
            point=point,
            replicas=replicas,
            payloads=payloads,
            run_id=run_id,
            duration_seconds=duration_seconds,
            client_max_in_flight=config.client_max_in_flight,
            request_timeout_seconds=config.request_timeout_seconds,
            stop=stop,
            capture=True,
        )
    finally:
        finished.set()
    samples = await sampler
    return phase, samples


def evaluate_point_guardrails(
    load: Mapping[str, Any],
    resources: Mapping[str, Any],
    config: S3RuntimeConfig,
) -> dict[str, Any]:
    checks = {
        "error_rate": float(load.get("error_rate", 1)) <= config.maximum_error_rate,
        "p99_latency": (
            float(dict(load.get("latency_ms", {})).get("p99", math.inf))
            <= config.maximum_p99_ms
        ),
        "host_cpu": (
            float(dict(resources.get("host_cpu_percent", {})).get("max", math.inf))
            <= config.maximum_host_cpu_percent
        ),
        "api_process_tree_rss": (
            float(
                dict(resources.get("api_process_tree_rss_bytes", {})).get(
                    "max", math.inf
                )
            )
            <= config.maximum_process_tree_rss_bytes
        ),
        "load_generator_start_lag": (
            float(
                dict(load.get("start_lag_ms", {})).get("p99", math.inf)
            )
            <= config.maximum_load_generator_start_lag_ms
        ),
        "load_generator_schedule_complete": (
            int(load.get("unscheduled_request_count", 0)) == 0
        ),
    }
    return {
        "checks": checks,
        "within_guardrails": all(checks.values()),
        "crossed": sorted(key for key, passed in checks.items() if not passed),
    }


def evaluate_point_assertions(payload: Mapping[str, Any]) -> dict[str, bool]:
    if payload.get("failure"):
        return {"runtime_completed": False}
    measurement = dict(payload.get("measurement", {}))
    load = summarize_load_phase(measurement)
    trace = dict(payload.get("trace", {}))
    cleanup = dict(payload.get("cleanup", {}))
    resources = resource_summary(payload.get("resource_samples", []))
    expected_traces = int(trace.get("expected_sampled_trace_count", 0))
    return {
        "runtime_completed": not bool(measurement.get("stopped")),
        "requests_observed": int(load.get("request_count", 0)) > 0,
        "transport_errors_accounted": (
            int(load.get("transport_error_count", -1))
            == int(dict(load.get("status_counts", {})).get("0", 0))
        ),
        "client_request_identity_complete": (
            int(load.get("client_request_identity_count", -1))
            == int(load.get("request_count", 0))
        ),
        "response_trace_identity_complete": (
            int(load.get("trace_identity_match_count", -1))
            == int(load.get("server_response_count", 0))
        ),
        "sampled_trace_nonzero": expected_traces > 0,
        "sampled_trace_chain_complete": (
            int(trace.get("complete_sampled_trace_count", -1)) == expected_traces
        ),
        "prometheus_targets_up": all(
            int(value) == 1
            for value in dict(payload.get("prometheus_targets", {})).values()
        ),
        "terminal_gauges_zero": all(
            float(value) == 0
            for value in dict(payload.get("terminal_gauges", {})).values()
        ),
        "api_process_tree_rss_nonzero": (
            float(
                dict(resources.get("api_process_tree_rss_bytes", {})).get("max", 0)
            )
            > 0
        ),
        "load_generator_rss_nonzero": (
            float(
                dict(resources.get("load_generator_rss_bytes", {})).get("max", 0)
            )
            > 0
        ),
        "cleanup_complete": (
            int(cleanup.get("lingering_pid_count", 1)) == 0
            and int(cleanup.get("marker_process_count", 1)) == 0
            and bool(cleanup.get("prometheus_container_absent"))
            and not cleanup.get("errors")
        ),
    }


def public_point_projection(payload: Mapping[str, Any]) -> dict[str, Any]:
    measurement = dict(payload.get("measurement", {}))
    return {
        "schema_version": "evm.s3_capacity_point_public.v1",
        "started_at": payload.get("started_at"),
        "finished_at": payload.get("finished_at"),
        "run_identity_sha256": hashlib.sha256(
            str(payload.get("run_id", "")).encode("utf-8")
        ).hexdigest(),
        "point": payload.get("point"),
        "repetition": payload.get("repetition"),
        "source_revision": payload.get("source_revision"),
        "runtime_config_sha256": payload.get("runtime_config_sha256"),
        "payload_sequence_sha256": payload.get("payload_sequence_sha256"),
        "load": summarize_load_phase(measurement) if measurement else {},
        "resources": resource_summary(payload.get("resource_samples", [])),
        "metrics": _public_metrics(payload.get("metrics", {})),
        "measurement_metric_delta": payload.get("measurement_metric_delta", {}),
        "trace": {
            key: value
            for key, value in dict(payload.get("trace", {})).items()
            if key not in {"complete_trace_ids", "task_span_names"}
        },
        "guardrails": payload.get("guardrails", {}),
        "assertions": payload.get("assertions", {}),
        "evidence_valid": bool(payload.get("evidence_valid")),
        "cleanup": payload.get("cleanup", {}),
        "failure": payload.get("failure"),
    }


def _docker_container_exists(container_name: str) -> bool:
    if not container_name:
        return False
    result = subprocess.run(
        ["docker", "inspect", container_name],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    return result.returncode == 0


def _public_metrics(payload: Any) -> dict[str, Any]:
    source = dict(payload) if isinstance(payload, Mapping) else {}
    return {
        key: source.get(key, {})
        for key in (
            "terminal_gauges",
            "observed_peaks",
            "counters",
            "wait_histograms",
        )
    }


def run_capacity_suite(
    *,
    root: Path,
    data_root: Path,
    config_path: Path,
    private_parent: Path,
    trace_path: Path,
    output_path: Path,
    selected_point_ids: Sequence[str] | None = None,
    repetitions_override: int | None = None,
) -> dict[str, Any]:
    root = root.resolve()
    config = S3RuntimeConfig.from_path(config_path, data_root=data_root)
    identity = verify_runtime_identity(config)
    source_revision, source_branch = source_identity(root)
    all_points = config.points()
    selected = set(selected_point_ids or ())
    points = [point for point in all_points if not selected or point.point_id in selected]
    if selected and {point.point_id for point in points} != selected:
        missing = sorted(selected - {point.point_id for point in points})
        raise S3RuntimeError(f"s3_requested_points_missing:{missing}")
    repetitions = repetitions_override or config.repetitions
    if repetitions < 1 or repetitions > config.repetitions:
        raise S3RuntimeError("s3_repetition_override_invalid")
    closure_eligible = not selected and repetitions == config.repetitions
    suite_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:6]
    suite_root = private_parent.resolve() / suite_id
    suite_root.mkdir(parents=True, exist_ok=False)
    results: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    started_at = utc_now()
    crossed_curves: set[tuple[str, str]] = set()
    for point in points:
        curve = (point.probe_family, point.mode)
        if point.matrix_scope == "baseline" and curve in crossed_curves:
            skipped.append(
                {
                    "point_id": point.point_id,
                    "reason": "higher_load_after_guardrail_boundary",
                }
            )
            continue
        point_results = []
        for repetition in range(1, repetitions + 1):
            result = run_capacity_point(
                root=root,
                data_root=data_root,
                suite_root=suite_root,
                config=config,
                point=point,
                repetition=repetition,
                source_revision=source_revision,
                source_branch=source_branch,
                trace_path=trace_path,
            )
            results.append(result)
            point_results.append(result)
            canonical_write(
                suite_root / "suite-progress-private.json",
                {
                    "schema_version": "evm.s3_capacity_suite_progress.v1",
                    "results": results,
                    "failures": failures,
                    "skipped": skipped,
                },
            )
            if not result.get("evidence_valid"):
                failures.append(
                    {
                        "point_id": point.point_id,
                        "repetition": repetition,
                        "reason": "point_evidence_invalid",
                        "failed_assertions": sorted(
                            key
                            for key, value in dict(
                                result.get("assertions", {})
                            ).items()
                            if not value
                        ),
                    }
                )
                break
        if failures:
            break
        if (
            point.matrix_scope == "baseline"
            and len(point_results) == repetitions
            and all(
                not bool(
                    dict(result.get("guardrails", {})).get(
                        "within_guardrails", False
                    )
                )
                for result in point_results
            )
        ):
            crossed_curves.add(curve)

    analysis = analyze_capacity_results(
        results=results,
        skipped=skipped,
        config=config,
        closure_eligible=closure_eligible,
    )
    private_index = private_evidence_index(suite_root)
    canonical_write(suite_root / "private-evidence-index.json", private_index)
    public = {
        "schema_version": "evm.s3_capacity_experiment.v1",
        "generated_at": utc_now(),
        "started_at": started_at,
        "source_identity": {
            "implementation_revision": source_revision,
            "branch": source_branch,
            "runtime_module_sha256": file_sha256(Path(__file__)),
        },
        "identity": identity,
        "runtime_contract": {
            "sha256": config.sha256,
            "seed": config.seed,
            "repetitions": repetitions,
            "closure_eligible": closure_eligible,
            "closed": {
                "warmup_seconds": config.closed_warmup_seconds,
                "measurement_seconds": config.closed_measurement_seconds,
                "cooldown_seconds": config.closed_cooldown_seconds,
                "steps": list(config.closed_steps),
            },
            "open": {
                "warmup_seconds": config.open_warmup_seconds,
                "measurement_seconds": config.open_measurement_seconds,
                "cooldown_seconds": config.open_cooldown_seconds,
                "requests_per_second": list(config.open_rates),
            },
        },
        "environment": {
            "api_transport": "external_tcp_http",
            "api_runtime": "real_existing_uvicorn_fastapi_application",
            "model_runtime": "existing_s3_capacity_probe_subroute",
            "observability": "isolated_prometheus_and_w3c_otlp",
            "load_generator": "co_located_and_measured_separately",
            "physical_scope": "one_local_physical_node",
            "customer_traffic": False,
        },
        "point_results": results,
        "skipped_points": skipped,
        "failed_attempts_and_rca": failures,
        "analysis": analysis,
        "acceptance": analysis["acceptance"],
        "runtime_verdict": analysis["runtime_verdict"],
        "scenario_status": analysis["scenario_status"],
        "private_evidence": {
            "artifact_count": private_index["artifact_count"],
            "aggregate_sha256": private_index["aggregate_sha256"],
            "location": "outside_git_private_evidence_root",
        },
        "claim_boundary": CLAIM_BOUNDARY,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    canonical_write(output_path, public)
    canonical_write(suite_root / "suite-summary-private.json", public)
    return public


def analyze_capacity_results(
    *,
    results: Sequence[Mapping[str, Any]],
    skipped: Sequence[Mapping[str, Any]],
    config: S3RuntimeConfig,
    closure_eligible: bool,
) -> dict[str, Any]:
    grouped: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for result in results:
        point = dict(result.get("point", {}))
        point_id = S3LoadPoint(**point).point_id if point else "missing"
        grouped[point_id].append(result)
    aggregates = [
        aggregate_point_repetitions(point_results)
        for point_results in grouped.values()
    ]
    aggregates.sort(
        key=lambda item: (
            str(item["point"]["probe_family"]),
            str(item["point"]["mode"]),
            str(item["point"]["matrix_scope"]),
            int(item["point"]["api_replicas"]),
            int(item["point"]["cpu_workers"]),
            float(item["point"]["load"]),
        )
    )
    baseline = [
        item for item in aggregates if item["point"]["matrix_scope"] == "baseline"
    ]
    curves: dict[str, list[dict[str, Any]]] = {}
    for family in PROBE_FAMILIES:
        for mode in ("closed", "open"):
            key = f"{family}:{mode}"
            curves[key] = sorted(
                [
                    item
                    for item in baseline
                    if item["point"]["probe_family"] == family
                    and item["point"]["mode"] == mode
                ],
                key=lambda item: float(item["point"]["load"]),
            )
    bottleneck = identify_first_bottleneck(curves, aggregates, config)
    sustainable = select_sustainable_points(curves, config)
    capacity = recalculate_s2_capacity(sustainable, config)
    curve_complete = all(
        len(points) >= 2
        and all(
            int(point["repetition_count"]) == config.repetitions
            and bool(point["evidence_valid"])
            for point in points
        )
        for points in curves.values()
    )
    metrics_complete = all(
        int(point["load_summary"]["latency_ms"]["count"]) > 0
        and int(point["resource_summary"]["api_process_tree_rss_bytes"]["count"])
        > 0
        for points in curves.values()
        for point in points
    )
    trace_complete = all(
        bool(point["trace_complete"]) for point in aggregates
    )
    topology_points = [
        point
        for point in aggregates
        if point["point"]["matrix_scope"] == "topology"
    ]
    topology_complete = (
        len(topology_points)
        == len(
            [
                point
                for point in config.points()
                if point.matrix_scope == "topology"
            ]
        )
        and all(
            int(point["repetition_count"]) == config.repetitions
            and bool(point["evidence_valid"])
            for point in topology_points
        )
    )
    acceptance = {
        "S3-AC-01": (
            closure_eligible and curve_complete and metrics_complete
        ),
        "S3-AC-02": (
            closure_eligible
            and trace_complete
            and topology_complete
            and bottleneck["identified"]
        ),
        "S3-AC-03": (
            closure_eligible
            and len(sustainable) == len(PROBE_FAMILIES) * 2
            and all(
                bool(item["selected"])
                and bool(item["below_or_equal_peak"])
                and bool(item["lower_than_peak_when_required"])
                for item in sustainable.values()
            )
        ),
        "S3-AC-04": (
            closure_eligible
            and bool(capacity.get("formula"))
            and float(capacity.get("measured_service_rate_per_second", 0)) > 0
            and int(capacity.get("selected_depth", 0)) > 0
        ),
    }
    passed = all(acceptance.values()) and not any(
        not bool(result.get("evidence_valid")) for result in results
    )
    return stable_public_projection({
        "aggregated_points": aggregates,
        "curves": curves,
        "bottleneck": bottleneck,
        "sustainable_operating_points": sustainable,
        "s2_capacity_recalculation": capacity,
        "topology_comparison_complete": topology_complete,
        "trace_complete": trace_complete,
        "skipped_point_count": len(skipped),
        "acceptance": acceptance,
        "runtime_verdict": "passed" if passed else "not_passed",
        "scenario_status": (
            "exercised_pending_git_blob_closure" if passed else "implementing"
        ),
    })


def aggregate_point_repetitions(
    results: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    point = dict(results[0]["point"])
    services = [float(item["load"]["service_rate_per_second"]) for item in results]
    errors = [float(item["load"]["error_rate"]) for item in results]
    p95 = [float(item["load"]["latency_ms"]["p95"]) for item in results]
    p99 = [float(item["load"]["latency_ms"]["p99"]) for item in results]
    offered = [float(item["load"]["offered_rate_per_second"]) for item in results]
    resources = {
        field: _statistics(
            [
                float(item["resources"][field]["max"])
                for item in results
            ]
        )
        for field in (
            "host_cpu_percent",
            "host_memory_percent",
            "api_process_tree_rss_bytes",
            "api_process_tree_cpu_percent",
            "load_generator_rss_bytes",
            "load_generator_cpu_percent",
        )
    }
    queue_wait = [
        float(item["load"]["server_timings_ms"]["queue_wait_ms"]["p99"])
        for item in results
    ]
    prediction = [
        float(item["load"]["server_timings_ms"]["prediction_ms"]["p99"])
        for item in results
    ]
    server_total = [
        float(item["load"]["server_timings_ms"]["total_ms"]["p99"])
        for item in results
    ]
    client_minus_server = [
        max(0.0, client_p99 - server_p99)
        for client_p99, server_p99 in zip(p99, server_total, strict=True)
    ]
    guardrails = [
        bool(dict(item.get("guardrails", {})).get("within_guardrails"))
        for item in results
    ]
    return {
        "point": point,
        "repetition_count": len(results),
        "evidence_valid": all(bool(item.get("evidence_valid")) for item in results),
        "within_guardrails": all(guardrails),
        "guardrail_pass_count": sum(guardrails),
        "load_summary": {
            "offered_rate_per_second": _statistics(offered),
            "service_rate_per_second": _statistics(services),
            "error_rate": _statistics(errors),
            "latency_ms": {
                "count": sum(
                    int(item["load"]["latency_ms"]["count"])
                    for item in results
                ),
                "p95": _statistics(p95),
                "p99": _statistics(p99),
            },
            "queue_wait_p99_ms": _statistics(queue_wait),
            "prediction_p99_ms": _statistics(prediction),
            "server_total_p99_ms": _statistics(server_total),
            "client_minus_server_p99_ms": _statistics(client_minus_server),
        },
        "resource_summary": resources,
        "trace_complete": all(
            int(item["trace"]["complete_sampled_trace_count"])
            == int(item["trace"]["expected_sampled_trace_count"])
            for item in results
        ),
        "private_evidence_sha256": [
            str(item["private_evidence_sha256"]) for item in results
        ],
    }


def identify_first_bottleneck(
    curves: Mapping[str, Sequence[Mapping[str, Any]]],
    aggregates: Sequence[Mapping[str, Any]],
    config: S3RuntimeConfig,
) -> dict[str, Any]:
    candidates: list[dict[str, Any]] = []
    for curve_name, points in curves.items():
        for index, point in enumerate(points):
            if point["within_guardrails"]:
                continue
            load = dict(point["load_summary"])
            resources = dict(point["resource_summary"])
            error = float(load["error_rate"]["mean"])
            p99 = float(load["latency_ms"]["p99"]["mean"])
            host_cpu = float(resources["host_cpu_percent"]["mean"])
            api_cpu = float(resources["api_process_tree_cpu_percent"]["mean"])
            load_generator_cpu = float(
                resources["load_generator_cpu_percent"]["mean"]
            )
            queue_wait = float(load["queue_wait_p99_ms"]["mean"])
            prediction = float(load["prediction_p99_ms"]["mean"])
            server_total = float(load["server_total_p99_ms"]["mean"])
            client_minus_server = float(
                load["client_minus_server_p99_ms"]["mean"]
            )
            if error > config.maximum_error_rate:
                cause = "api_admission_or_capacity_rejection"
            elif (
                p99 > config.maximum_p99_ms
                and client_minus_server > max(server_total, 50.0)
                and load_generator_cpu >= 90.0
                and queue_wait > max(prediction * 2, 1.0)
            ):
                cause = "co_located_client_http_and_process_local_worker_queue"
            elif queue_wait > max(prediction * 2, 1.0):
                cause = "process_local_worker_queue"
            elif host_cpu > config.maximum_host_cpu_percent:
                cause = "host_cpu_saturation"
            elif p99 > config.maximum_p99_ms:
                cause = "api_tail_latency"
            else:
                cause = "capacity_curve_inflection"
            candidates.append(
                {
                    "curve": curve_name,
                    "point_index": index,
                    "load": point["point"]["load"],
                    "cause": cause,
                    "signals": {
                        "error_rate": error,
                        "p99_ms": p99,
                        "host_cpu_percent": host_cpu,
                        "api_process_tree_cpu_percent": api_cpu,
                        "load_generator_cpu_percent": load_generator_cpu,
                        "queue_wait_p99_ms": queue_wait,
                        "prediction_p99_ms": prediction,
                        "server_total_p99_ms": server_total,
                        "client_minus_server_p99_ms": client_minus_server,
                    },
                }
            )
            break
    topology = [
        point
        for point in aggregates
        if point["point"]["matrix_scope"] == "topology"
    ]
    return {
        "identified": bool(candidates),
        "first_observed": candidates[0] if candidates else None,
        "curve_boundaries": candidates,
        "topology_point_count": len(topology),
        "method": (
            "first frozen guardrail crossing, attributed with client-versus-"
            "server latency, queue-wait, prediction, API/load-generator CPU, "
            "error, W3C span, and topology telemetry"
        ),
        "signal_source": "recomputed_from_persisted_point_results",
        "attribution_boundary": (
            "The load generator and API share one physical host, so client "
            "HTTP scheduling and host contention cannot be isolated as a "
            "remote-generator network result."
        ),
    }


def select_sustainable_points(
    curves: Mapping[str, Sequence[Mapping[str, Any]]],
    config: S3RuntimeConfig,
) -> dict[str, Any]:
    selected: dict[str, Any] = {}
    for curve_name, points in curves.items():
        candidates = [
            point
            for point in points
            if point["within_guardrails"] and point["evidence_valid"]
        ]
        choice = (
            max(
                candidates,
                key=lambda point: float(
                    point["load_summary"]["service_rate_per_second"]["mean"]
                ),
            )
            if candidates
            else None
        )
        peak = (
            max(
                points,
                key=lambda point: float(
                    point["load_summary"]["service_rate_per_second"]["mean"]
                ),
            )
            if points
            else None
        )
        selected_rate = (
            float(choice["load_summary"]["service_rate_per_second"]["mean"])
            if choice
            else 0.0
        )
        peak_rate = (
            float(peak["load_summary"]["service_rate_per_second"]["mean"])
            if peak
            else 0.0
        )
        lower_than_peak_required = bool(
            peak and not bool(peak.get("within_guardrails"))
        )
        selected[curve_name] = {
            "selected": bool(choice),
            "load": choice["point"]["load"] if choice else None,
            "service_rate_per_second": (
                choice["load_summary"]["service_rate_per_second"]["mean"]
                if choice
                else 0
            ),
            "p99_ms": (
                choice["load_summary"]["latency_ms"]["p99"]["mean"]
                if choice
                else 0
            ),
            "error_rate": (
                choice["load_summary"]["error_rate"]["mean"] if choice else 1
            ),
            "peak_service_rate_per_second": (
                peak_rate
            ),
            "below_or_equal_peak": bool(
                choice and peak and selected_rate <= peak_rate
            ),
            "lower_than_peak_required": lower_than_peak_required,
            "lower_than_peak_when_required": bool(
                choice
                and peak
                and (
                    not lower_than_peak_required
                    or selected_rate < peak_rate
                )
            ),
            "selection_rule": (
                "highest measured service rate satisfying frozen error, p99, "
                "host CPU, RSS, and load-generator lag guardrails"
            ),
        }
    return selected


def stable_public_projection(value: Any) -> Any:
    """Normalize derived evidence numbers without changing raw observations."""
    if isinstance(value, Mapping):
        return {
            str(key): stable_public_projection(item)
            for key, item in value.items()
        }
    if isinstance(value, tuple):
        return [stable_public_projection(item) for item in value]
    if isinstance(value, list):
        return [stable_public_projection(item) for item in value]
    if isinstance(value, float):
        if not math.isfinite(value):
            raise S3RuntimeError("s3_non_finite_projection")
        try:
            quantized = Decimal(str(value)).quantize(
                _PUBLIC_PROJECTION_QUANTUM,
                rounding=ROUND_HALF_EVEN,
            )
        except InvalidOperation as exc:
            raise S3RuntimeError("s3_projection_quantization_failed") from exc
        normalized = float(quantized)
        return 0.0 if normalized == 0 else normalized
    return value


def recalculate_s2_capacity(
    sustainable: Mapping[str, Mapping[str, Any]],
    config: S3RuntimeConfig,
) -> dict[str, Any]:
    rates = [
        float(item.get("service_rate_per_second", 0))
        for name, item in sustainable.items()
        if name.endswith(":open") and bool(item.get("selected"))
    ]
    service_rate = min(rates) if rates else 0.0
    calculated = max(
        1,
        int(
            math.floor(
                service_rate
                * config.maximum_queue_wait_seconds
                * config.capacity_safety_factor
            )
        ),
    ) if service_rate > 0 else 0
    selected = (
        calculated
        if config.allow_automatic_increase
        else min(config.prior_depth, calculated)
    ) if calculated else 0
    return {
        "formula": "floor(service_rate_rps * maximum_queue_wait_seconds * safety_factor)",
        "measured_service_rate_per_second": service_rate,
        "service_rate_selection": "minimum sustainable open-loop rate across probes",
        "maximum_queue_wait_seconds": config.maximum_queue_wait_seconds,
        "safety_factor": config.capacity_safety_factor,
        "calculated_depth": calculated,
        "prior_depth": config.prior_depth,
        "selected_depth": selected,
        "automatic_increase_allowed": config.allow_automatic_increase,
        "rollback_depth": config.rollback_depth,
        "units": {
            "service_rate": "requests/second",
            "queue_wait": "seconds",
            "depth": "requests",
        },
    }


def private_evidence_index(suite_root: Path) -> dict[str, Any]:
    artifacts = []
    for path in sorted(suite_root.rglob("*")):
        if path.is_file():
            artifacts.append(
                {
                    "path": path.relative_to(suite_root).as_posix(),
                    "sha256": file_sha256(path),
                    "bytes": path.stat().st_size,
                }
            )
    return {
        "schema_version": "evm.s3_private_evidence_index.v1",
        "generated_at": utc_now(),
        "artifacts": artifacts,
        "artifact_count": len(artifacts),
        "aggregate_sha256": canonical_digest(artifacts),
    }
