from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import subprocess
import sys
import threading
import time
import tomllib
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence
from uuid import uuid4

import psutil
import requests
from prometheus_client.parser import text_string_to_metric_families

from evm.control_panel.admission_queue import AdmissionQueueConfig
from evm.control_panel.transactional_store import canonical_digest
from evm.scale_validation.evidence import write_public_json
from evm.scale_validation.s1_runtime import (
    ApiRuntime,
    available_port,
    canonical_write,
    drop_schema,
    materialize_isolated_profile,
    runtime_environment,
    schema_identifier,
    sha256_file,
    source_revision,
    start_api,
    trace_id,
    traceparent,
    utc_now,
)


SCHEMA_VERSION = "evm.s2_external_runtime_evidence.v1"
EXPECTED_PROFILE_IDS = tuple("ABCDEFGHIJ")
TERMINAL_QUEUE_STATES = {"completed", "failed", "dlq", "expired", "cancelled"}
ACTIVE_QUEUE_STATES = {
    "available",
    "retry_wait",
    "leased",
    "runtime_pending",
    "outcome_unknown",
}
FULL_TRACE_NAMES = {
    "POST /control-panel/v1/tasks",
    "queue.enqueue",
    "task_queue.execute",
    "task_queue.executor",
    "queue.dispatch",
    "airflow.rest",
}
CLAIM_BOUNDARY = (
    "Controlled external HTTP, PostgreSQL, process, Prometheus, OTLP, and one-GPU "
    "evidence on one local physical node. No customer traffic, production SLA, "
    "physical-node HA, DR, or multi-GPU claim."
)


@dataclass(frozen=True)
class S2MatrixConfig:
    path: Path
    version: str
    seed: int
    repetitions: int
    warmup_seconds: float
    sample_interval_seconds: float
    drain_timeout_seconds: float
    trace_flush_seconds: float
    prometheus_scrape_interval_seconds: float
    rss_slope_measurement_seconds: float
    profiles: dict[str, dict[str, Any]]
    sha256: str

    @classmethod
    def from_path(cls, path: str | Path) -> S2MatrixConfig:
        resolved = Path(path).resolve()
        raw = resolved.read_bytes()
        payload = tomllib.loads(raw.decode("utf-8"))
        matrix = dict(payload.get("matrix", {}))
        profiles = {
            str(key): dict(value)
            for key, value in dict(payload.get("profiles", {})).items()
        }
        if tuple(sorted(profiles)) != EXPECTED_PROFILE_IDS:
            raise ValueError("S2 matrix must define the exact A-J profile set")
        if str(matrix.get("status")) != "frozen-before-experiment":
            raise ValueError("S2 matrix must be frozen before execution")
        repetitions = int(matrix.get("repetitions", 0))
        if repetitions != 3:
            raise ValueError("S2 closure requires exactly three independent repetitions")
        numeric = {
            "warmup_seconds": float(matrix.get("warmup_seconds", 0)),
            "sample_interval_seconds": float(matrix.get("sample_interval_seconds", 0)),
            "drain_timeout_seconds": float(matrix.get("drain_timeout_seconds", 0)),
            "trace_flush_seconds": float(matrix.get("trace_flush_seconds", 0)),
            "prometheus_scrape_interval_seconds": float(
                matrix.get("prometheus_scrape_interval_seconds", 0)
            ),
            "rss_slope_measurement_seconds": float(
                matrix.get("rss_slope_measurement_seconds", 0)
            ),
        }
        if any(value <= 0 for value in numeric.values()):
            raise ValueError("S2 matrix timing values must be positive")
        if (
            float(profiles["D"].get("arrival_duration_seconds", 0))
            < numeric["rss_slope_measurement_seconds"]
        ):
            raise ValueError("profile D must cover the frozen RSS slope window")
        return cls(
            path=resolved,
            version=str(matrix["version"]),
            seed=int(matrix["seed"]),
            repetitions=repetitions,
            profiles=profiles,
            sha256=hashlib.sha256(raw).hexdigest(),
            **numeric,
        )


@dataclass
class ManagedProcess:
    process: subprocess.Popen[str]
    label: str
    stdout_path: Path
    stderr_path: Path
    stdout_stream: Any
    stderr_stream: Any

    @property
    def pid(self) -> int:
        return int(self.process.pid)

    def stop(self, *, force: bool = False) -> list[int]:
        stopped = terminate_process_tree(self.pid, force=force)
        self.stdout_stream.close()
        self.stderr_stream.close()
        return stopped


@dataclass
class PrometheusRuntime:
    container_name: str
    base_url: str
    config_path: Path
    targets_path: Path

    def stop(self) -> None:
        subprocess.run(
            ["docker", "rm", "-f", self.container_name],
            check=False,
            capture_output=True,
            text=True,
        )


@dataclass
class RuntimeScope:
    root: Path
    private_root: Path
    profile_root: Path
    database_url: str
    schema: str
    revision: str
    branch: str
    queue_config_path: Path
    fixture: ManagedProcess
    api: ApiRuntime
    environment: dict[str, str]
    fixture_url: str
    trace_offset: int
    marker: str
    worker: ManagedProcess | None = None
    prometheus: PrometheusRuntime | None = None
    worker_heartbeat: Path | None = None
    stopped_pids: list[int] = field(default_factory=list)

    def start_worker(self) -> ManagedProcess:
        if self.worker is not None and self.worker.process.poll() is None:
            return self.worker
        heartbeat = self.private_root / "task-queue-worker-heartbeat.json"
        heartbeat.unlink(missing_ok=True)
        worker = start_managed_process(
            command=[
                sys.executable,
                "-m",
                "evm.control_panel.task_queue_worker",
                "--config",
                str(self.queue_config_path),
                "--worker-id",
                self.marker,
                "--heartbeat-path",
                str(heartbeat),
            ],
            cwd=self.root,
            environment=self.environment,
            private_root=self.private_root,
            label=f"worker-{self.marker}",
        )
        self.worker = worker
        self.worker_heartbeat = heartbeat
        wait_for_worker_heartbeat(worker, heartbeat)
        return worker

    def stop_worker(self, *, force: bool = False) -> list[int]:
        if self.worker is None:
            return []
        stopped = self.worker.stop(force=force)
        self.stopped_pids.extend(stopped)
        self.worker = None
        return stopped

    def start_prometheus(self, scrape_interval_seconds: float) -> PrometheusRuntime:
        if self.worker is None:
            raise RuntimeError("worker must be running before Prometheus starts")
        runtime = start_isolated_prometheus(
            private_root=self.private_root,
            marker=self.marker,
            api_port=self.api.port,
            worker_port=AdmissionQueueConfig.from_path(
                self.queue_config_path
            ).metrics_port,
            scrape_interval_seconds=scrape_interval_seconds,
        )
        self.prometheus = runtime
        return runtime

    def close(self) -> dict[str, Any]:
        errors: list[str] = []
        if self.prometheus is not None:
            self.prometheus.stop()
            self.prometheus = None
        try:
            self.stop_worker()
        except Exception as exc:
            errors.append(f"worker:{type(exc).__name__}")
        try:
            self.api.stop()
        except Exception as exc:
            errors.append(f"api:{type(exc).__name__}")
        try:
            self.fixture.stop()
        except Exception as exc:
            errors.append(f"fixture:{type(exc).__name__}")
        try:
            drop_schema(self.database_url, self.schema)
        except Exception as exc:
            errors.append(f"schema:{type(exc).__name__}")
        lingering = marker_processes(self.marker)
        return {
            "schema_dropped": not schema_exists(self.database_url, self.schema),
            "marker_processes_remaining": lingering,
            "stopped_process_count": len(set(self.stopped_pids)),
            "errors": errors,
        }


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
    process = subprocess.Popen(
        list(command),
        cwd=cwd,
        env=dict(environment),
        stdout=stdout_stream,
        stderr=stderr_stream,
        text=True,
    )
    return ManagedProcess(
        process=process,
        label=label,
        stdout_path=stdout_path,
        stderr_path=stderr_path,
        stdout_stream=stdout_stream,
        stderr_stream=stderr_stream,
    )


def wait_for_http(url: str, process: ManagedProcess, *, timeout: float = 30.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.process.poll() is not None:
            raise RuntimeError(f"{process.label}_exited:{process.stderr_path}")
        try:
            response = requests.get(url, timeout=1)
            if response.status_code == 200:
                return
        except requests.RequestException:
            pass
        time.sleep(0.2)
    raise RuntimeError(f"{process.label}_start_timeout:{process.stderr_path}")


def wait_for_worker_heartbeat(
    worker: ManagedProcess,
    heartbeat_path: Path,
    *,
    timeout: float = 30.0,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if worker.process.poll() is not None:
            raise RuntimeError(f"queue_worker_exited:{worker.stderr_path}")
        if heartbeat_path.is_file():
            try:
                payload = json.loads(heartbeat_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                payload = {}
            if payload.get("status") in {"online", "degraded"}:
                return payload
        time.sleep(0.1)
    raise RuntimeError(f"queue_worker_heartbeat_timeout:{heartbeat_path}")


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


def process_tree_rss(pid: int | None) -> dict[str, int]:
    if not pid:
        return {"parent": 0, "children": 0, "total": 0, "child_count": 0}
    try:
        parent = psutil.Process(pid)
        parent_rss = int(parent.memory_info().rss)
        children = parent.children(recursive=True)
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return {"parent": 0, "children": 0, "total": 0, "child_count": 0}
    child_rss = 0
    live_children = 0
    for child in children:
        try:
            child_rss += int(child.memory_info().rss)
            live_children += 1
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return {
        "parent": parent_rss,
        "children": child_rss,
        "total": parent_rss + child_rss,
        "child_count": live_children,
    }


def start_fixture(
    *, root: Path, private_root: Path, environment: Mapping[str, str], marker: str
) -> tuple[ManagedProcess, str]:
    port = available_port()
    process = start_managed_process(
        command=[
            sys.executable,
            "-m",
            "uvicorn",
            "evm.scale_validation.s2_airflow_fixture:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--workers",
            "1",
            "--log-level",
            "warning",
        ],
        cwd=root,
        environment=environment,
        private_root=private_root,
        label=f"fixture-{marker}",
    )
    base_url = f"http://127.0.0.1:{port}"
    wait_for_http(f"{base_url}/health", process)
    return process, base_url


def start_isolated_prometheus(
    *,
    private_root: Path,
    marker: str,
    api_port: int,
    worker_port: int,
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
                '  - job_name: "s2-isolated"',
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
    targets_path.write_text(
        json.dumps(
            [
                {
                    "targets": [f"host.docker.internal:{api_port}"],
                    "labels": {"component": "api"},
                },
                {
                    "targets": [f"host.docker.internal:{worker_port}"],
                    "labels": {"component": "worker"},
                },
            ],
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
        newline="\n",
    )
    container_name = f"evm-s2-prom-{marker}"[:63]
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
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"isolated_prometheus_start_failed:{result.stderr.strip()}")
    runtime = PrometheusRuntime(
        container_name=container_name,
        base_url=f"http://127.0.0.1:{port}",
        config_path=config_path,
        targets_path=targets_path,
    )
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        try:
            ready = requests.get(f"{runtime.base_url}/-/ready", timeout=1)
            if ready.status_code == 200:
                return runtime
        except requests.RequestException:
            pass
        time.sleep(0.2)
    runtime.stop()
    raise RuntimeError("isolated_prometheus_ready_timeout")


def prometheus_query(runtime: PrometheusRuntime, query: str) -> list[dict[str, Any]]:
    response = requests.get(
        f"{runtime.base_url}/api/v1/query",
        params={"query": query},
        timeout=5,
    )
    response.raise_for_status()
    payload = response.json()
    if payload.get("status") != "success":
        raise RuntimeError(f"prometheus_query_failed:{query}")
    return list(payload.get("data", {}).get("result", []))


def wait_prometheus_targets(runtime: PrometheusRuntime, *, timeout: float = 20.0) -> dict[str, int]:
    deadline = time.monotonic() + timeout
    last: dict[str, int] = {}
    while time.monotonic() < deadline:
        result = prometheus_query(runtime, 'up{job="s2-isolated"}')
        last = {
            str(item.get("metric", {}).get("component")): int(float(item["value"][1]))
            for item in result
        }
        if last.get("api") == 1 and last.get("worker") == 1:
            return last
        time.sleep(0.5)
    raise RuntimeError(f"prometheus_targets_not_up:{last}")


def schema_exists(database_url: str, schema: str) -> bool:
    import psycopg

    with psycopg.connect(database_url) as connection:
        row = connection.execute(
            "SELECT EXISTS(SELECT 1 FROM information_schema.schemata WHERE schema_name=%s)",
            (schema,),
        ).fetchone()
    return bool(row[0])


def database_snapshot(database_url: str, schema: str) -> dict[str, Any]:
    import psycopg
    from psycopg.rows import dict_row

    schema_identifier(schema)
    with psycopg.connect(database_url, row_factory=dict_row) as connection:
        queue = connection.execute(
            f"""
            SELECT task_id, state, resource_class, payload_bytes, claim_count,
                   attempt_count, lease_epoch, terminal_reason, last_failure_class,
                   created_at, execution_started_at, terminal_at
            FROM {schema}.task_admission_queue
            ORDER BY created_at, queue_id
            """
        ).fetchall()
        effects = connection.execute(
            f"""
            SELECT task_id, state, dag_id, dag_run_id, lease_epoch, runtime_state
            FROM {schema}.task_dispatch_effects
            ORDER BY task_id
            """
        ).fetchall()
        history = connection.execute(
            f"""
            SELECT history_class, terminal_state, item_count, payload_bytes
            FROM {schema}.task_history_rollups
            ORDER BY history_class, terminal_state
            """
        ).fetchall()
        retry_budget = connection.execute(
            f"""
            SELECT budget_name, consumed FROM {schema}.task_retry_budget
            ORDER BY budget_name
            """
        ).fetchall()
        idempotency = connection.execute(
            f"""
            SELECT count(*) AS rows,
                   count(*) FILTER (WHERE compacted_at IS NOT NULL) AS tombstones,
                   COALESCE(sum(pg_column_size(idempotency_keys)), 0) AS bytes
            FROM {schema}.idempotency_keys
            WHERE entity_kind='task_assignment'
            """
        ).fetchone()
        task_states = connection.execute(
            f"""
            SELECT state, count(*) AS count
            FROM {schema}.entities
            WHERE entity_kind='task_assignment'
            GROUP BY state ORDER BY state
            """
        ).fetchall()
        mirror = connection.execute(
            f"""
            SELECT COALESCE(jsonb_array_length(payload), 0) AS rows,
                   COALESCE(pg_column_size(payload), 0) AS bytes
            FROM {schema}.collections WHERE collection_name='task_assignments'
            """
        ).fetchone()
    def normalize(row: Mapping[str, Any]) -> dict[str, Any]:
        return {
            key: value.isoformat() if isinstance(value, datetime) else value
            for key, value in dict(row).items()
        }

    queue_rows = [normalize(row) for row in queue]
    state_counts = Counter(str(row["state"]) for row in queue_rows)
    state_bytes = Counter()
    for row in queue_rows:
        state_bytes[str(row["state"])] += int(row["payload_bytes"])
    return {
        "queue": queue_rows,
        "effects": [normalize(row) for row in effects],
        "history": [normalize(row) for row in history],
        "retry_budget": [normalize(row) for row in retry_budget],
        "idempotency": normalize(idempotency or {}),
        "task_states": {str(row["state"]): int(row["count"]) for row in task_states},
        "mirror": normalize(mirror or {"rows": 0, "bytes": 0}),
        "state_counts": dict(state_counts),
        "state_bytes": dict(state_bytes),
        "active_depth": sum(state_counts[state] for state in ACTIVE_QUEUE_STATES),
        "active_bytes": sum(state_bytes[state] for state in ACTIVE_QUEUE_STATES),
        "terminal_depth": sum(state_counts[state] for state in TERMINAL_QUEUE_STATES),
    }


def expire_task(database_url: str, schema: str, task_id: str) -> None:
    import psycopg

    schema_identifier(schema)
    with psycopg.connect(database_url) as connection:
        updated = connection.execute(
            f"""
            UPDATE {schema}.task_admission_queue
            SET deadline_at=clock_timestamp() - interval '1 second'
            WHERE task_id=%s AND state='available'
            """,
            (task_id,),
        ).rowcount
        connection.commit()
    if updated != 1:
        raise RuntimeError("expiry_injection_identity_mismatch")


def metric_samples(text: str) -> dict[str, list[dict[str, Any]]]:
    samples: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for family in text_string_to_metric_families(text):
        for sample in family.samples:
            samples[sample.name].append(
                {"labels": dict(sample.labels), "value": float(sample.value)}
            )
    return dict(samples)


def fetch_metrics(url: str) -> dict[str, list[dict[str, Any]]]:
    response = requests.get(url, timeout=5)
    response.raise_for_status()
    return metric_samples(response.text)


def max_metric(
    samples: Mapping[str, Sequence[Mapping[str, Any]]],
    name: str,
    *,
    labels: Mapping[str, str] | None = None,
) -> float:
    values = []
    for sample in samples.get(name, []):
        sample_labels = dict(sample.get("labels", {}))
        if labels and any(sample_labels.get(key) != value for key, value in labels.items()):
            continue
        values.append(float(sample.get("value", 0)))
    return max(values, default=0.0)


def build_task_payload(
    *,
    profile_id: str,
    repetition: int,
    index: int,
    idempotency_key: str,
    failure_mode: str = "healthy",
    resource_profile: str = "cpu-local",
    terminal_state: str = "success",
    terminal_after_seconds: float = 0.0,
    delay_seconds: float = 0.0,
    padding_chunks: int = 0,
    padding_chunk_bytes: int = 0,
    cuda_probe: bool = False,
    seed: int = 0,
) -> dict[str, Any]:
    config_payload: dict[str, Any] = {
        "dag_id": "s2_validation",
        "s2_profile": profile_id,
        "s2_repetition": repetition,
        "s2_failure_mode": failure_mode,
        "s2_terminal_state": terminal_state,
        "s2_terminal_after_seconds": terminal_after_seconds,
        "s2_delay_seconds": delay_seconds,
    }
    if padding_chunks:
        config_payload["s2_padding"] = [
            chr(65 + (index + offset) % 26) * padding_chunk_bytes
            for offset in range(padding_chunks)
        ]
    if cuda_probe:
        config_payload.update({"s2_cuda_probe": True, "s2_cuda_seed": seed})
    return {
        "cycle_id": "s2-controlled-validation",
        "task_type": "airflow_dag_run",
        "owner": "s2-validation",
        "priority": "normal",
        "resource_profile": resource_profile,
        "config_payload": config_payload,
        "requester_team": "platform-validation",
        "approval_policy": "automatic",
        "dry_run": False,
        "idempotency_key": idempotency_key,
    }


def payload_digest(payloads: Sequence[Mapping[str, Any]]) -> str:
    generalized = []
    for payload in payloads:
        item = json.loads(json.dumps(payload))
        item.pop("idempotency_key", None)
        config = item.get("config_payload")
        if isinstance(config, dict):
            config["s2_repetition"] = 0
        generalized.append(item)
    return canonical_digest(generalized)


def submit_payloads(
    *,
    api_url: str,
    payloads: Sequence[dict[str, Any]],
    trace_seeds: Sequence[str],
    concurrency: int,
    timeout: float = 10.0,
) -> dict[str, Any]:
    if len(payloads) != len(trace_seeds):
        raise ValueError("payload and trace seed counts differ")
    lock = threading.Lock()
    in_flight = 0
    peak_in_flight = 0
    results: list[dict[str, Any]] = []

    def send(index: int) -> dict[str, Any]:
        nonlocal in_flight, peak_in_flight
        with lock:
            in_flight += 1
            peak_in_flight = max(peak_in_flight, in_flight)
        started = time.monotonic()
        try:
            response = requests.post(
                f"{api_url}/control-panel/v1/tasks",
                json=payloads[index],
                headers={"traceparent": traceparent(trace_seeds[index])},
                timeout=timeout,
            )
            try:
                body = response.json()
            except ValueError:
                body = {"raw": response.text[:500]}
            return {
                "index": index,
                "status_code": response.status_code,
                "body": body,
                "retry_after": response.headers.get("Retry-After"),
                "elapsed_seconds": time.monotonic() - started,
                "trace_id": trace_id(traceparent(trace_seeds[index])),
            }
        except requests.RequestException as exc:
            return {
                "index": index,
                "status_code": 0,
                "body": {"error": type(exc).__name__},
                "retry_after": None,
                "elapsed_seconds": time.monotonic() - started,
                "trace_id": trace_id(traceparent(trace_seeds[index])),
            }
        finally:
            with lock:
                in_flight = max(0, in_flight - 1)

    with ThreadPoolExecutor(max_workers=min(concurrency, len(payloads))) as pool:
        futures = [pool.submit(send, index) for index in range(len(payloads))]
        for future in as_completed(futures):
            results.append(future.result())
    results.sort(key=lambda item: int(item["index"]))
    return {
        "results": results,
        "peak_in_flight": peak_in_flight,
        "status_counts": dict(Counter(str(item["status_code"]) for item in results)),
    }


def submit_payloads_at_rate(
    *,
    api_url: str,
    payloads: Sequence[dict[str, Any]],
    trace_seeds: Sequence[str],
    concurrency: int,
    rate_per_second: float,
    duration_seconds: float,
    timeout: float = 10.0,
) -> dict[str, Any]:
    if len(payloads) != len(trace_seeds):
        raise ValueError("payload and trace seed counts differ")
    if rate_per_second <= 0 or duration_seconds <= 0:
        raise ValueError("scheduled arrival rate and duration must be positive")
    expected_duration = (len(payloads) - 1) / rate_per_second if payloads else 0.0
    if expected_duration > duration_seconds:
        raise ValueError("payload count exceeds the frozen arrival window")
    lock = threading.Lock()
    in_flight = 0
    peak_in_flight = 0
    results: list[dict[str, Any]] = []

    def send(index: int) -> dict[str, Any]:
        nonlocal in_flight, peak_in_flight
        with lock:
            in_flight += 1
            peak_in_flight = max(peak_in_flight, in_flight)
        started = time.monotonic()
        try:
            response = requests.post(
                f"{api_url}/control-panel/v1/tasks",
                json=payloads[index],
                headers={"traceparent": traceparent(trace_seeds[index])},
                timeout=timeout,
            )
            try:
                body = response.json()
            except ValueError:
                body = {"raw": response.text[:500]}
            return {
                "index": index,
                "status_code": response.status_code,
                "body": body,
                "retry_after": response.headers.get("Retry-After"),
                "elapsed_seconds": time.monotonic() - started,
                "trace_id": trace_id(traceparent(trace_seeds[index])),
            }
        except requests.RequestException as exc:
            return {
                "index": index,
                "status_code": 0,
                "body": {"error": type(exc).__name__},
                "retry_after": None,
                "elapsed_seconds": time.monotonic() - started,
                "trace_id": trace_id(traceparent(trace_seeds[index])),
            }
        finally:
            with lock:
                in_flight = max(0, in_flight - 1)

    started = time.monotonic()
    with ThreadPoolExecutor(max_workers=min(concurrency, len(payloads))) as pool:
        futures = []
        for index in range(len(payloads)):
            target = started + index / rate_per_second
            remaining = target - time.monotonic()
            if remaining > 0:
                time.sleep(remaining)
            futures.append(pool.submit(send, index))
        for future in as_completed(futures):
            results.append(future.result())
    observed_window = time.monotonic() - started
    results.sort(key=lambda item: int(item["index"]))
    return {
        "results": results,
        "peak_in_flight": peak_in_flight,
        "status_counts": dict(Counter(str(item["status_code"]) for item in results)),
        "declared_rate_per_second": rate_per_second,
        "declared_duration_seconds": duration_seconds,
        "observed_arrival_window_seconds": observed_window,
    }


def accepted_tasks(submission: Mapping[str, Any]) -> dict[str, str]:
    accepted: dict[str, str] = {}
    for item in submission.get("results", []):
        if int(item.get("status_code", 0)) != 202:
            continue
        body = item.get("body", {})
        task_id = body.get("task_id") if isinstance(body, Mapping) else None
        if isinstance(task_id, str):
            accepted[task_id] = str(item.get("trace_id"))
    return accepted


def retry_rejected_payloads(
    *,
    api_url: str,
    payloads: Sequence[dict[str, Any]],
    trace_seeds: Sequence[str],
    initial_submission: Mapping[str, Any],
    max_rounds: int,
    concurrency: int,
    retry_after_cap_seconds: int,
) -> tuple[list[dict[str, Any]], set[int]]:
    """Retry only bounded 429 admissions with their original idempotency identity."""
    pending_indices = {
        int(item["index"])
        for item in initial_submission.get("results", [])
        if int(item.get("status_code", 0)) == 429
    }
    retries: list[dict[str, Any]] = []
    for _round in range(max_rounds):
        if not pending_indices:
            break
        previous_results = [
            item
            for item in (
                initial_submission.get("results", []) if not retries else retries[-1]["results"]
            )
            if int(item.get("original_index", item["index"])) in pending_indices
        ]
        retry_after_values = [
            int(item["retry_after"])
            for item in previous_results
            if str(item.get("retry_after") or "").isdigit()
        ]
        if retry_after_values:
            time.sleep(min(max(retry_after_values), retry_after_cap_seconds))

        ordered_indices = sorted(pending_indices)
        retry = submit_payloads(
            api_url=api_url,
            payloads=[payloads[index] for index in ordered_indices],
            trace_seeds=[trace_seeds[index] for index in ordered_indices],
            concurrency=min(concurrency, len(ordered_indices)),
        )
        for item in retry["results"]:
            item["original_index"] = ordered_indices[int(item["index"])]
        retries.append(retry)
        pending_indices = {
            int(item["original_index"])
            for item in retry["results"]
            if int(item.get("status_code", 0)) == 429
        }
    return retries, pending_indices


def summarize_submission(submission: Mapping[str, Any]) -> dict[str, Any]:
    results = list(submission.get("results", []))
    retry_after = [
        int(item["retry_after"])
        for item in results
        if str(item.get("retry_after") or "").isdigit()
    ]
    latencies = sorted(float(item.get("elapsed_seconds", 0)) for item in results)
    p95_index = max(0, min(len(latencies) - 1, int(len(latencies) * 0.95) - 1))
    return {
        "submitted": len(results),
        "status_counts": dict(submission.get("status_counts", {})),
        "peak_client_in_flight": int(submission.get("peak_in_flight", 0)),
        "retry_after_values": sorted(set(retry_after)),
        "p95_seconds": latencies[p95_index] if latencies else 0.0,
        "transport_failures": sum(1 for item in results if item.get("status_code") == 0),
    }


def collect_runtime_sample(scope: RuntimeScope) -> dict[str, Any]:
    snapshot = database_snapshot(scope.database_url, scope.schema)
    api_rss = process_tree_rss(scope.api.process.pid)
    worker_rss = process_tree_rss(scope.worker.pid if scope.worker else None)
    return {
        "monotonic": time.monotonic(),
        "active_depth": snapshot["active_depth"],
        "active_bytes": snapshot["active_bytes"],
        "terminal_depth": snapshot["terminal_depth"],
        "state_counts": snapshot["state_counts"],
        "api_rss": api_rss,
        "worker_rss": worker_rss,
    }


def process_tree_rss_slope(
    samples: Sequence[Mapping[str, Any]],
    *,
    window_seconds: float,
) -> dict[str, Any]:
    if not samples:
        return {"measured": False, "window_seconds": 0.0, "sample_count": 0}
    end = float(samples[-1]["monotonic"])
    selected = [
        item
        for item in samples
        if end - float(item["monotonic"]) <= window_seconds
    ]
    if len(selected) < 2:
        return {
            "measured": False,
            "window_seconds": 0.0,
            "sample_count": len(selected),
        }
    start = float(selected[0]["monotonic"])
    elapsed = end - start
    if elapsed < window_seconds * 0.95:
        return {
            "measured": False,
            "window_seconds": elapsed,
            "sample_count": len(selected),
        }
    x_values = [float(item["monotonic"]) - start for item in selected]

    def slope(key: str) -> float:
        values = [
            float(item[f"{key}_rss"]["total"])
            for item in selected
        ]
        x_mean = sum(x_values) / len(x_values)
        y_mean = sum(values) / len(values)
        denominator = sum((value - x_mean) ** 2 for value in x_values)
        if denominator <= 0:
            return 0.0
        per_second = sum(
            (x_value - x_mean) * (y_value - y_mean)
            for x_value, y_value in zip(x_values, values, strict=True)
        ) / denominator
        return per_second * 60.0

    return {
        "measured": True,
        "window_seconds": elapsed,
        "sample_count": len(selected),
        "api_bytes_per_minute": slope("api"),
        "worker_bytes_per_minute": slope("worker"),
    }


def wait_for_terminal(
    scope: RuntimeScope,
    accepted_ids: set[str],
    *,
    timeout: float,
    sample_interval: float,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    samples: list[dict[str, Any]] = []
    terminal_seen: set[str] = set()
    started = time.monotonic()
    while time.monotonic() < deadline:
        sample = collect_runtime_sample(scope)
        snapshot = database_snapshot(scope.database_url, scope.schema)
        for row in snapshot["queue"]:
            if str(row["state"]) in TERMINAL_QUEUE_STATES:
                terminal_seen.add(str(row["task_id"]))
        sample["terminal_seen_count"] = len(terminal_seen)
        samples.append(sample)
        if accepted_ids.issubset(terminal_seen) and snapshot["active_depth"] == 0:
            break
        time.sleep(sample_interval)
    final = database_snapshot(scope.database_url, scope.schema)
    for row in final["queue"]:
        if str(row["state"]) in TERMINAL_QUEUE_STATES:
            terminal_seen.add(str(row["task_id"]))
    peaks = {
        "active_depth": max((int(item["active_depth"]) for item in samples), default=0),
        "active_bytes": max((int(item["active_bytes"]) for item in samples), default=0),
        "api_process_tree_rss_bytes": max(
            (int(item["api_rss"]["total"]) for item in samples), default=0
        ),
        "worker_process_tree_rss_bytes": max(
            (int(item["worker_rss"]["total"]) for item in samples), default=0
        ),
        "executor_children_rss_bytes": max(
            (int(item["worker_rss"]["children"]) for item in samples), default=0
        ),
    }
    return {
        "closed": accepted_ids.issubset(terminal_seen) and final["active_depth"] == 0,
        "elapsed_seconds": time.monotonic() - started,
        "accepted_count": len(accepted_ids),
        "terminal_seen_count": len(accepted_ids & terminal_seen),
        "terminal_seen_task_ids": sorted(accepted_ids & terminal_seen),
        "missing_terminal_count": len(accepted_ids - terminal_seen),
        "peaks": peaks,
        "samples": samples,
        "final": final,
    }


def merge_terminal_results(
    results: Sequence[Mapping[str, Any]],
    accepted_ids: set[str],
) -> dict[str, Any]:
    if not results:
        raise ValueError("terminal result set cannot be empty")
    terminal_ids = {
        str(task_id)
        for result in results
        for task_id in result.get("terminal_seen_task_ids", [])
    }
    peak_keys = (
        "active_depth",
        "active_bytes",
        "api_process_tree_rss_bytes",
        "worker_process_tree_rss_bytes",
        "executor_children_rss_bytes",
    )
    return {
        "closed": accepted_ids == terminal_ids
        and all(bool(result.get("closed")) for result in results),
        "elapsed_seconds": sum(float(result.get("elapsed_seconds", 0)) for result in results),
        "accepted_count": len(accepted_ids),
        "terminal_seen_count": len(terminal_ids),
        "terminal_seen_task_ids": sorted(terminal_ids),
        "missing_terminal_count": len(accepted_ids - terminal_ids),
        "peaks": {
            key: max(
                int(result.get("peaks", {}).get(key, 0)) for result in results
            )
            for key in peak_keys
        },
        "samples": [
            sample
            for result in results
            for sample in result.get("samples", [])
        ],
        "final": dict(results[-1].get("final", {})),
    }


def trace_summary(path: Path, offset: int, task_traces: Mapping[str, str]) -> dict[str, Any]:
    by_trace: dict[str, set[str]] = defaultdict(set)
    if not path.is_file():
        return {"task_count": len(task_traces), "complete_count": 0, "missing": len(task_traces)}
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
                    trace_identity = str(span.get("traceId") or "")
                    name = str(span.get("name") or "")
                    if trace_identity and name:
                        by_trace[trace_identity].add(name)
    task_names = {task_id: sorted(by_trace.get(identity, set())) for task_id, identity in task_traces.items()}
    complete = {
        task_id
        for task_id, names in task_names.items()
        if FULL_TRACE_NAMES.issubset(set(names))
    }
    return {
        "task_count": len(task_traces),
        "complete_count": len(complete),
        "missing": len(task_traces) - len(complete),
        "complete_task_ids": sorted(complete),
        "task_span_names": task_names,
        "raw_tail_sha256": hashlib.sha256(raw).hexdigest(),
        "raw_tail_bytes": len(raw),
    }


def public_trace_summary(payload: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "task_count": int(payload.get("task_count", 0)),
        "complete_count": int(payload.get("complete_count", 0)),
        "missing": int(payload.get("missing", 0)),
        "raw_tail_sha256": str(payload.get("raw_tail_sha256", "")),
        "raw_tail_bytes": int(payload.get("raw_tail_bytes", 0)),
    }


def create_runtime_scope(
    *,
    root: Path,
    private_root: Path,
    profile_root: Path,
    database_url: str,
    schema: str,
    revision: str,
    branch: str,
    queue_config_path: Path,
    trace_path: Path,
    marker: str,
) -> RuntimeScope:
    private_root.mkdir(parents=True, exist_ok=False)
    trace_offset = trace_path.stat().st_size if trace_path.is_file() else 0
    data_root = private_root / "runtime-data"
    environment = runtime_environment(
        root=root,
        data_root=data_root,
        profile_root=profile_root,
        database_url=database_url,
        schema=schema,
        revision=revision,
        branch=branch,
        pool_max_size=32,
        acquire_timeout_seconds=1.0,
        lock_timeout_seconds=2.0,
    )
    environment.update(
        {
            "APP_NAME": "evm-s2-isolated-api",
            "EVM_TASK_ADMISSION_MODE": "durable",
            "EVM_TASK_QUEUE_CONFIG": str(queue_config_path),
            "EVM_CONTROL_PLANE_AUTO_MIGRATE": "true",
            "OTEL_SERVICE_NAMESPACE": "enterprise-mlops-s2",
            "EVM_S2_RUNTIME_MARKER": marker,
        }
    )
    fixture, fixture_url = start_fixture(
        root=root,
        private_root=private_root,
        environment=environment,
        marker=marker,
    )
    environment["EVM_AIRFLOW_API_URL"] = f"{fixture_url}/api/v1"
    try:
        api = start_api(
            root=root,
            private_root=private_root,
            environment=environment,
            label=marker,
        )
    except Exception:
        fixture.stop()
        drop_schema(database_url, schema)
        raise
    return RuntimeScope(
        root=root,
        private_root=private_root,
        profile_root=profile_root,
        database_url=database_url,
        schema=schema,
        revision=revision,
        branch=branch,
        queue_config_path=queue_config_path,
        fixture=fixture,
        api=api,
        environment=environment,
        fixture_url=fixture_url,
        trace_offset=trace_offset,
        marker=marker,
    )


def database_mirror_parity(
    database_url: str,
    schema: str,
    task_file: Path,
) -> dict[str, Any]:
    import psycopg
    from psycopg.rows import dict_row

    schema_identifier(schema)
    with psycopg.connect(database_url, row_factory=dict_row) as connection:
        entities = connection.execute(
            f"""
            SELECT payload FROM {schema}.entities
            WHERE entity_kind='task_assignment'
            ORDER BY entity_id
            """
        ).fetchall()
        collection = connection.execute(
            f"""
            SELECT payload FROM {schema}.collections
            WHERE collection_name='task_assignments'
            """
        ).fetchone()
    authority = [dict(row["payload"]) for row in entities]
    mirror = list(collection["payload"]) if collection else []
    file_payload: list[dict[str, Any]] = []
    if task_file.is_file():
        loaded = json.loads(task_file.read_text(encoding="utf-8-sig"))
        if isinstance(loaded, list):
            file_payload = [dict(item) for item in loaded if isinstance(item, Mapping)]
    def task_key(item: Mapping[str, Any]) -> str:
        return str(item.get("task_id", ""))

    authority_digest = canonical_digest(sorted(authority, key=task_key))
    mirror_digest = canonical_digest(sorted(mirror, key=task_key))
    file_digest = canonical_digest(sorted(file_payload, key=task_key))
    return {
        "authority_count": len(authority),
        "mirror_count": len(mirror),
        "file_count": len(file_payload),
        "authority_sha256": authority_digest,
        "mirror_sha256": mirror_digest,
        "file_sha256": file_digest,
        "matches": authority_digest == mirror_digest == file_digest,
    }


def wait_for_mirror_parity(scope: RuntimeScope, *, timeout: float = 10.0) -> dict[str, Any]:
    task_file = Path(scope.environment["EVM_CONTROL_PANEL_LEDGER_ROOT"]) / "task_assignments.json"
    deadline = time.monotonic() + timeout
    last: dict[str, Any] = {"matches": False}
    while time.monotonic() < deadline:
        try:
            last = database_mirror_parity(
                scope.database_url,
                scope.schema,
                task_file,
            )
        except (OSError, json.JSONDecodeError):
            last = {"matches": False}
        if last.get("matches"):
            return last
        time.sleep(0.2)
    return last


def fixture_evidence(scope: RuntimeScope) -> dict[str, Any]:
    response = requests.get(f"{scope.fixture_url}/evidence", timeout=5)
    response.raise_for_status()
    return dict(response.json())


def worker_metrics(scope: RuntimeScope) -> dict[str, list[dict[str, Any]]]:
    config = AdmissionQueueConfig.from_path(scope.queue_config_path)
    return fetch_metrics(f"http://127.0.0.1:{config.metrics_port}/metrics")


def api_metrics(scope: RuntimeScope) -> dict[str, list[dict[str, Any]]]:
    return fetch_metrics(f"{scope.api.base_url}/metrics")


def bounded_metric_summary(
    api: Mapping[str, Sequence[Mapping[str, Any]]],
    worker: Mapping[str, Sequence[Mapping[str, Any]]],
) -> dict[str, Any]:
    admission = {
        f"{sample['labels'].get('outcome')}:{sample['labels'].get('reason')}": sample["value"]
        for sample in api.get("evm_task_queue_admissions_total", [])
    }
    terminals = {
        f"{sample['labels'].get('state')}:{sample['labels'].get('reason')}": sample["value"]
        for sample in worker.get("evm_task_queue_terminal_total", [])
    }
    retries = {
        f"{sample['labels'].get('outcome')}:{sample['labels'].get('failure_class')}": sample["value"]
        for sample in worker.get("evm_task_queue_retries_total", [])
    }
    dlq = {
        str(sample["labels"].get("reason")): sample["value"]
        for sample in worker.get("evm_task_queue_dlq_total", [])
    }
    cpu_scale = {
        str(sample["labels"].get("direction")): sample["value"]
        for sample in worker.get("evm_task_queue_cpu_scale_events_total", [])
    }
    return {
        "admission": admission,
        "terminal": terminals,
        "retry": retries,
        "dlq": dlq,
        "cpu_scale": cpu_scale,
        "rss_bytes": max_metric(worker, "evm_task_queue_process_rss_bytes"),
        "rss_slope_bytes_per_minute": max_metric(
            worker,
            "evm_task_queue_process_rss_slope_bytes_per_minute",
        ),
        "cpu_capacity": max_metric(
            worker,
            "evm_task_queue_worker_capacity",
            labels={"resource_class": "cpu"},
        ),
        "gpu_capacity": max_metric(
            worker,
            "evm_task_queue_worker_capacity",
            labels={"resource_class": "gpu"},
        ),
    }


def assertion(assertion_id: str, passed: bool, observed: Any) -> dict[str, Any]:
    return {"assertion_id": assertion_id, "passed": bool(passed), "observed": observed}


def profile_payloads(
    *,
    profile_id: str,
    repetition: int,
    count: int,
    seed: int,
    failure_modes: Sequence[str] | None = None,
    resource_profile: str = "cpu-local",
    terminal_state: str = "success",
    terminal_after_seconds: float = 0.0,
    delay_seconds: float = 0.0,
    padding_chunks: int = 0,
    padding_chunk_bytes: int = 0,
    cuda_probe: bool = False,
) -> tuple[list[dict[str, Any]], list[str]]:
    modes = list(failure_modes or ["healthy"] * count)
    if len(modes) != count:
        raise ValueError("failure mode count mismatch")
    order = list(range(count))
    random.Random(seed).shuffle(order)
    payloads: list[dict[str, Any]] = []
    traces: list[str] = []
    for position, index in enumerate(order):
        key = f"s2-{profile_id.lower()}-r{repetition}-{index:04d}"
        payloads.append(
            build_task_payload(
                profile_id=profile_id,
                repetition=repetition,
                index=index,
                idempotency_key=key,
                failure_mode=modes[index],
                resource_profile=resource_profile,
                terminal_state=terminal_state,
                terminal_after_seconds=terminal_after_seconds,
                delay_seconds=delay_seconds,
                padding_chunks=padding_chunks,
                padding_chunk_bytes=padding_chunk_bytes,
                cuda_probe=cuda_probe,
                seed=seed,
            )
        )
        traces.append(f"s2:{profile_id}:{repetition}:{position}:{seed}")
    return payloads, traces


def merge_submissions(submissions: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    results: list[dict[str, Any]] = []
    peak = 0
    for submission in submissions:
        results.extend(dict(item) for item in submission.get("results", []))
        peak = max(peak, int(submission.get("peak_in_flight", 0)))
    return {
        "results": results,
        "peak_in_flight": peak,
        "status_counts": dict(Counter(str(item.get("status_code")) for item in results)),
    }


def finalize_profile_scope(
    *,
    scope: RuntimeScope,
    profile_id: str,
    repetition: int,
    matrix: S2MatrixConfig,
    queue_config: AdmissionQueueConfig,
    submissions: Sequence[Mapping[str, Any]],
    accepted: Mapping[str, str],
    terminal: Mapping[str, Any],
    trace_expected: Mapping[str, str],
    effect_expected: set[str],
    no_effect_expected: set[str],
    assertions: list[dict[str, Any]],
    input_sequence_sha256: str,
    extra: Mapping[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    time.sleep(matrix.trace_flush_seconds)
    trace = trace_summary(
        Path(
            os.getenv(
                "EVM_S2_OTEL_TRACE_PATH",
                "F:/EnterpriseMLOps_Data/enterprise-vision-mlops/artifacts/scale_validation/otel/traces.json",
            )
        ),
        scope.trace_offset,
        trace_expected,
    )
    fixture = fixture_evidence(scope)
    api_sample = api_metrics(scope)
    worker_sample = worker_metrics(scope)
    metric_summary = bounded_metric_summary(api_sample, worker_sample)
    targets = wait_prometheus_targets(scope.prometheus) if scope.prometheus else {}
    prometheus_rss = (
        prometheus_query(scope.prometheus, "evm_task_queue_process_rss_bytes")
        if scope.prometheus
        else []
    )
    parity = wait_for_mirror_parity(scope)
    logical_effects = {
        str(task_id): list(values)
        for task_id, values in dict(fixture.get("logical_task_effects", {})).items()
    }
    expected_effect_ok = all(len(logical_effects.get(task_id, [])) == 1 for task_id in effect_expected)
    no_effect_ok = all(len(logical_effects.get(task_id, [])) == 0 for task_id in no_effect_expected)
    generic_assertions = [
        assertion(
            "accepted_equals_terminal",
            bool(terminal.get("closed"))
            and int(terminal.get("accepted_count", 0))
            == int(terminal.get("terminal_seen_count", -1))
            and set(accepted)
            == set(str(item) for item in terminal.get("terminal_seen_task_ids", [])),
            {
                "accepted": terminal.get("accepted_count"),
                "terminal": terminal.get("terminal_seen_count"),
                "missing": terminal.get("missing_terminal_count"),
            },
        ),
        assertion(
            "active_queue_drained",
            int(terminal.get("final", {}).get("active_depth", -1)) == 0,
            terminal.get("final", {}).get("state_counts", {}),
        ),
        assertion(
            "outcome_unknown_zero",
            int(terminal.get("final", {}).get("state_counts", {}).get("outcome_unknown", 0))
            == 0,
            terminal.get("final", {}).get("state_counts", {}).get("outcome_unknown", 0),
        ),
        assertion(
            "logical_effect_exactly_once",
            expected_effect_ok
            and no_effect_ok
            and int(fixture.get("duplicate_external_effects", -1)) == 0
            and int(fixture.get("tasks_with_multiple_logical_effects", -1)) == 0,
            {
                "expected_nonzero": len(effect_expected),
                "observed_unique": fixture.get("unique_external_effects"),
                "duplicates": fixture.get("duplicate_external_effects"),
                "multiple_logical": fixture.get("tasks_with_multiple_logical_effects"),
            },
        ),
        assertion(
            "trace_chain_complete",
            int(trace.get("missing", -1)) == 0,
            public_trace_summary(trace),
        ),
        assertion("postgres_json_mirror_parity", bool(parity.get("matches")), parity),
        assertion(
            "prometheus_targets_up",
            targets.get("api") == 1 and targets.get("worker") == 1,
            targets,
        ),
        assertion(
            "process_tree_rss_nonzero",
            int(terminal.get("peaks", {}).get("api_process_tree_rss_bytes", 0)) > 0
            and int(terminal.get("peaks", {}).get("worker_process_tree_rss_bytes", 0)) > 0,
            terminal.get("peaks", {}),
        ),
    ]
    all_assertions = [*assertions, *generic_assertions]
    passed = all(bool(item["passed"]) for item in all_assertions)
    merged = merge_submissions(submissions)
    private_payload = {
        "schema_version": "evm.s2_profile_private.v1",
        "generated_at": utc_now(),
        "profile_id": profile_id,
        "repetition": repetition,
        "source_revision": scope.revision,
        "schema": scope.schema,
        "config": queue_config.public_dict(),
        "submissions": list(submissions),
        "accepted_task_traces": dict(accepted),
        "terminal": terminal,
        "trace": trace,
        "fixture": fixture,
        "metrics": {"summary": metric_summary, "prometheus_rss": prometheus_rss},
        "parity": parity,
        "assertions": all_assertions,
        "extra": dict(extra or {}),
        "passed": passed,
    }
    private_path = scope.private_root / "profile-result-private.json"
    canonical_write(private_path, private_payload)
    private_hash = sha256_file(private_path)
    public_payload = {
        "profile_id": profile_id,
        "name": matrix.profiles[profile_id]["name"],
        "repetition": repetition,
        "config_version": queue_config.profile_version,
        "config_sha256": queue_config.sha256,
        "input_sequence_sha256": input_sequence_sha256,
        "submission": summarize_submission(merged),
        "terminal": {
            "accepted_count": terminal.get("accepted_count"),
            "terminal_count": terminal.get("terminal_seen_count"),
            "missing_count": terminal.get("missing_terminal_count"),
            "elapsed_seconds": terminal.get("elapsed_seconds"),
            "final_state_counts": terminal.get("final", {}).get("state_counts", {}),
        },
        "peaks": terminal.get("peaks", {}),
        "metrics": metric_summary,
        "prometheus": {"targets": targets, "rss_query_series": len(prometheus_rss)},
        "trace": public_trace_summary(trace),
        "external_effects": {
            "attempts": fixture.get("attempts"),
            "unique": fixture.get("unique_external_effects"),
            "duplicates": fixture.get("duplicate_external_effects"),
            "multiple_logical": fixture.get("tasks_with_multiple_logical_effects"),
            "max_runtime_concurrency": fixture.get("max_runtime_concurrency", {}),
            "max_external_in_flight": fixture.get("max_external_in_flight", {}),
            "cuda_probe_count": fixture.get("cuda_probe_count", 0),
            "cuda_failure_count": fixture.get("cuda_failure_count", 0),
            "cuda_nonzero_activity_count": fixture.get("cuda_nonzero_activity_count", 0),
            "cuda_peak_allocated_bytes": fixture.get("cuda_peak_allocated_bytes", 0),
        },
        "history": {
            "rollups": terminal.get("final", {}).get("history", []),
            "idempotency": terminal.get("final", {}).get("idempotency", {}),
            "mirror": terminal.get("final", {}).get("mirror", {}),
        },
        "profile_observations": {
            key: value
            for key, value in dict(extra or {}).items()
            if key
            in {
                "accepted_completion_throughput_per_second",
                "declared_duration_seconds",
                "declared_rate_per_second",
                "observed_arrival_window_seconds",
                "rss_slope",
                "sustained_sample_count",
                "variant",
            }
        },
        "assertions": all_assertions,
        "private_evidence_sha256": private_hash,
        "passed": passed,
    }
    return public_payload, private_payload


def start_worker_and_monitoring(scope: RuntimeScope, matrix: S2MatrixConfig) -> None:
    scope.start_worker()
    scope.start_prometheus(matrix.prometheus_scrape_interval_seconds)
    wait_prometheus_targets(scope.prometheus)
    time.sleep(matrix.warmup_seconds)


def include_pre_admission_peak(terminal: dict[str, Any], snapshot: Mapping[str, Any]) -> None:
    peaks = terminal.setdefault("peaks", {})
    peaks["active_depth"] = max(
        int(peaks.get("active_depth", 0)),
        int(snapshot.get("active_depth", 0)),
    )
    peaks["active_bytes"] = max(
        int(peaks.get("active_bytes", 0)),
        int(snapshot.get("active_bytes", 0)),
    )


def run_profile_a(
    scope: RuntimeScope,
    matrix: S2MatrixConfig,
    repetition: int,
) -> dict[str, Any]:
    spec = matrix.profiles["A"]
    payloads, traces = profile_payloads(
        profile_id="A",
        repetition=repetition,
        count=int(spec["request_count"]),
        seed=matrix.seed,
        terminal_after_seconds=float(spec["terminal_after_seconds"]),
    )
    start_worker_and_monitoring(scope, matrix)
    submission = submit_payloads(
        api_url=scope.api.base_url,
        payloads=payloads,
        trace_seeds=traces,
        concurrency=int(spec["concurrency"]),
    )
    accepted = accepted_tasks(submission)
    terminal = wait_for_terminal(
        scope,
        set(accepted),
        timeout=matrix.drain_timeout_seconds,
        sample_interval=matrix.sample_interval_seconds,
    )
    return {
        "submissions": [submission],
        "accepted": accepted,
        "terminal": terminal,
        "trace_expected": accepted,
        "effect_expected": set(accepted),
        "no_effect_expected": set(),
        "assertions": [
            assertion(
                "A_all_requests_accepted",
                submission["status_counts"] == {"202": len(payloads)},
                submission["status_counts"],
            )
        ],
        "input_sequence_sha256": payload_digest(payloads),
    }


def run_profile_b(
    scope: RuntimeScope,
    matrix: S2MatrixConfig,
    repetition: int,
) -> dict[str, Any]:
    spec = matrix.profiles["B"]
    payloads, traces = profile_payloads(
        profile_id="B",
        repetition=repetition,
        count=int(spec["request_count"]),
        seed=matrix.seed + 100,
        terminal_after_seconds=float(spec["terminal_after_seconds"]),
        delay_seconds=float(spec["dispatch_delay_seconds"]),
    )
    fill_count = AdmissionQueueConfig.from_path(scope.queue_config_path).durable_max_depth
    fill = submit_payloads(
        api_url=scope.api.base_url,
        payloads=payloads[:fill_count],
        trace_seeds=traces[:fill_count],
        concurrency=8,
    )
    burst = submit_payloads(
        api_url=scope.api.base_url,
        payloads=payloads[fill_count:],
        trace_seeds=traces[fill_count:],
        concurrency=int(spec["concurrency"]),
    )
    merged = merge_submissions([fill, burst])
    accepted = accepted_tasks(merged)
    pre_snapshot = database_snapshot(scope.database_url, scope.schema)
    start_worker_and_monitoring(scope, matrix)
    terminal = wait_for_terminal(
        scope,
        set(accepted),
        timeout=matrix.drain_timeout_seconds,
        sample_interval=matrix.sample_interval_seconds,
    )
    include_pre_admission_peak(terminal, pre_snapshot)
    retry_after = [
        item.get("retry_after")
        for item in burst["results"]
        if item.get("status_code") == 429
    ]
    return {
        "submissions": [fill, burst],
        "accepted": accepted,
        "terminal": terminal,
        "trace_expected": accepted,
        "effect_expected": set(accepted),
        "no_effect_expected": set(),
        "assertions": [
            assertion(
                "B_depth_bound_reached",
                pre_snapshot["active_depth"]
                == AdmissionQueueConfig.from_path(scope.queue_config_path).durable_max_depth,
                pre_snapshot["active_depth"],
            ),
            assertion(
                "B_over_capacity_returns_429",
                int(burst["status_counts"].get("429", 0)) > 0
                and all(str(value).isdigit() for value in retry_after),
                {"status_counts": burst["status_counts"], "retry_after": retry_after},
            ),
        ],
        "input_sequence_sha256": payload_digest(payloads),
    }


def run_profile_c(
    scope: RuntimeScope,
    matrix: S2MatrixConfig,
    repetition: int,
) -> dict[str, Any]:
    spec = matrix.profiles["C"]
    payloads, traces = profile_payloads(
        profile_id="C",
        repetition=repetition,
        count=int(spec["request_count"]),
        seed=matrix.seed + 200,
        terminal_after_seconds=float(spec["terminal_after_seconds"]),
        delay_seconds=float(spec["dispatch_delay_seconds"]),
        padding_chunks=int(spec["padding_chunks"]),
        padding_chunk_bytes=int(spec["padding_chunk_bytes"]),
    )
    byte_burst = submit_payloads(
        api_url=scope.api.base_url,
        payloads=payloads,
        trace_seeds=traces,
        concurrency=int(spec["concurrency"]),
    )
    oversized_payload = dict(payloads[0])
    oversized_payload["s2_ingress_oversized"] = "x" * (
        AdmissionQueueConfig.from_path(scope.queue_config_path).ingress_max_body_bytes + 1024
    )
    oversized = submit_payloads(
        api_url=scope.api.base_url,
        payloads=[oversized_payload],
        trace_seeds=[f"s2:C:{repetition}:oversized"],
        concurrency=1,
    )
    accepted = accepted_tasks(byte_burst)
    pre_snapshot = database_snapshot(scope.database_url, scope.schema)
    start_worker_and_monitoring(scope, matrix)
    terminal = wait_for_terminal(
        scope,
        set(accepted),
        timeout=matrix.drain_timeout_seconds,
        sample_interval=matrix.sample_interval_seconds,
    )
    include_pre_admission_peak(terminal, pre_snapshot)
    return {
        "submissions": [byte_burst, oversized],
        "accepted": accepted,
        "terminal": terminal,
        "trace_expected": accepted,
        "effect_expected": set(accepted),
        "no_effect_expected": set(),
        "assertions": [
            assertion(
                "C_aggregate_bytes_returns_429",
                int(byte_burst["status_counts"].get("429", 0)) > 0,
                byte_burst["status_counts"],
            ),
            assertion(
                "C_single_oversized_returns_413",
                oversized["status_counts"] == {"413": 1},
                oversized["status_counts"],
            ),
        ],
        "input_sequence_sha256": payload_digest(payloads),
    }


def run_profile_d_variant(
    scope: RuntimeScope,
    matrix: S2MatrixConfig,
    repetition: int,
    *,
    variant: str,
) -> dict[str, Any]:
    spec = matrix.profiles["D"]
    active_config = AdmissionQueueConfig.from_path(scope.queue_config_path)
    profile_label = f"D-{variant}"
    payloads, traces = profile_payloads(
        profile_id=profile_label,
        repetition=repetition,
        count=int(spec["request_count"]),
        seed=matrix.seed + 300,
        terminal_after_seconds=float(spec["terminal_after_seconds"]),
        delay_seconds=float(spec["dispatch_delay_seconds"]),
    )
    start_worker_and_monitoring(scope, matrix)
    monitor_stop = threading.Event()
    sustained_samples: list[dict[str, Any]] = []

    def monitor() -> None:
        while not monitor_stop.is_set():
            try:
                sustained_samples.append(collect_runtime_sample(scope))
            except Exception:
                pass
            monitor_stop.wait(matrix.sample_interval_seconds)

    monitor_thread = threading.Thread(
        target=monitor,
        name=f"s2-d-{variant}-sampler",
        daemon=True,
    )
    monitor_thread.start()
    try:
        submission = submit_payloads_at_rate(
            api_url=scope.api.base_url,
            payloads=payloads,
            trace_seeds=traces,
            concurrency=int(spec["concurrency"]),
            rate_per_second=float(spec["arrival_rate_per_second"]),
            duration_seconds=float(spec["arrival_duration_seconds"]),
        )
        accepted = accepted_tasks(submission)
        terminal = wait_for_terminal(
            scope,
            set(accepted),
            timeout=matrix.drain_timeout_seconds,
            sample_interval=matrix.sample_interval_seconds,
        )
    finally:
        monitor_stop.set()
        monitor_thread.join(timeout=5)
    if sustained_samples:
        terminal["peaks"]["active_depth"] = max(
            int(terminal["peaks"].get("active_depth", 0)),
            max(int(item["active_depth"]) for item in sustained_samples),
        )
        terminal["peaks"]["active_bytes"] = max(
            int(terminal["peaks"].get("active_bytes", 0)),
            max(int(item["active_bytes"]) for item in sustained_samples),
        )
        terminal["peaks"]["api_process_tree_rss_bytes"] = max(
            int(terminal["peaks"].get("api_process_tree_rss_bytes", 0)),
            max(int(item["api_rss"]["total"]) for item in sustained_samples),
        )
        terminal["peaks"]["worker_process_tree_rss_bytes"] = max(
            int(terminal["peaks"].get("worker_process_tree_rss_bytes", 0)),
            max(int(item["worker_rss"]["total"]) for item in sustained_samples),
        )
        terminal["peaks"]["executor_children_rss_bytes"] = max(
            int(terminal["peaks"].get("executor_children_rss_bytes", 0)),
            max(int(item["worker_rss"]["children"]) for item in sustained_samples),
        )
    rss_slope = process_tree_rss_slope(
        sustained_samples,
        window_seconds=matrix.rss_slope_measurement_seconds,
    )
    status_counts = dict(submission["status_counts"])
    retry_after = {
        str(item.get("retry_after"))
        for item in submission["results"]
        if item.get("retry_after") is not None
    }
    if variant == "adaptive":
        admission_assertion = assertion(
            "D_adaptive_backpressure_is_explicit",
            int(status_counts.get("202", 0)) > 0
            and int(status_counts.get("429", 0)) > 0
            and retry_after == {str(active_config.retry_after_seconds)},
            {"status_counts": status_counts, "retry_after": sorted(retry_after)},
        )
    else:
        admission_assertion = assertion(
            "D_cpu1_backpressure_is_explicit",
            int(status_counts.get("202", 0)) > 0
            and int(status_counts.get("429", 0)) > 0
            and retry_after == {str(active_config.retry_after_seconds)},
            {"status_counts": status_counts, "retry_after": sorted(retry_after)},
        )
    rss_assertion = assertion(
        f"D_{variant}_rss_slope_bounded",
        bool(rss_slope.get("measured"))
        and float(rss_slope.get("api_bytes_per_minute", float("inf")))
        <= active_config.rss_slope_tolerance_bytes_per_minute
        and float(rss_slope.get("worker_bytes_per_minute", float("inf")))
        <= active_config.rss_slope_tolerance_bytes_per_minute,
        rss_slope,
    )
    total_window = (
        float(submission["observed_arrival_window_seconds"])
        + float(terminal["elapsed_seconds"])
    )
    return {
        "submissions": [submission],
        "accepted": accepted,
        "terminal": terminal,
        "trace_expected": accepted,
        "effect_expected": set(accepted),
        "no_effect_expected": set(),
        "assertions": [admission_assertion, rss_assertion],
        "input_sequence_sha256": payload_digest(payloads),
        "variant": variant,
        "extra": {
            "declared_rate_per_second": submission["declared_rate_per_second"],
            "declared_duration_seconds": submission["declared_duration_seconds"],
            "observed_arrival_window_seconds": submission[
                "observed_arrival_window_seconds"
            ],
            "sustained_sample_count": len(sustained_samples),
            "rss_slope": rss_slope,
            "accepted_completion_throughput_per_second": (
                len(accepted) / total_window if total_window > 0 else 0.0
            ),
        },
    }


def run_profile_e(
    scope: RuntimeScope,
    matrix: S2MatrixConfig,
    repetition: int,
) -> dict[str, Any]:
    spec = matrix.profiles["E"]
    payloads, traces = profile_payloads(
        profile_id="E",
        repetition=repetition,
        count=int(spec["request_count"]),
        seed=matrix.seed + 400,
        terminal_after_seconds=float(spec["terminal_after_seconds"]),
    )
    start_worker_and_monitoring(scope, matrix)
    submissions: list[dict[str, Any]] = []
    accepted: dict[str, str] = {}
    terminals: list[dict[str, Any]] = []
    batch_size = int(spec["batch_size"])
    for offset in range(0, len(payloads), batch_size):
        submission = submit_payloads(
            api_url=scope.api.base_url,
            payloads=payloads[offset : offset + batch_size],
            trace_seeds=traces[offset : offset + batch_size],
            concurrency=min(int(spec["concurrency"]), batch_size),
        )
        submissions.append(submission)
        retries, pending_indices = retry_rejected_payloads(
            api_url=scope.api.base_url,
            payloads=payloads[offset : offset + batch_size],
            trace_seeds=traces[offset : offset + batch_size],
            initial_submission=submission,
            max_rounds=int(spec["rejected_retry_max_rounds"]),
            concurrency=int(spec["rejected_retry_concurrency"]),
            retry_after_cap_seconds=int(spec["retry_after_cap_seconds"]),
        )
        submissions.extend(retries)
        batch_accepted = accepted_tasks(submission)
        for retry in retries:
            batch_accepted.update(accepted_tasks(retry))
        accepted.update(batch_accepted)
        terminal_batch = wait_for_terminal(
            scope,
            set(batch_accepted),
            timeout=matrix.drain_timeout_seconds,
            sample_interval=matrix.sample_interval_seconds,
        )
        terminals.append(terminal_batch)
        if pending_indices or not terminal_batch["closed"]:
            break
    terminal = merge_terminal_results(terminals, set(accepted))
    deadline = time.monotonic() + 15
    compacted = database_snapshot(scope.database_url, scope.schema)
    while time.monotonic() < deadline:
        compacted = database_snapshot(scope.database_url, scope.schema)
        compacted_count = sum(int(row["item_count"]) for row in compacted["history"])
        if compacted_count > 0 and int(compacted["idempotency"].get("tombstones", 0)) > 0:
            break
        time.sleep(0.2)
    effects_before = fixture_evidence(scope)
    old_worker_pid = scope.worker.pid if scope.worker else 0
    scope.stop_worker()
    scope.start_worker()
    new_worker_pid = scope.worker.pid if scope.worker else 0
    replay = submit_payloads(
        api_url=scope.api.base_url,
        payloads=[payloads[0]],
        trace_seeds=[f"s2:E:{repetition}:replay"],
        concurrency=1,
    )
    time.sleep(1.0)
    replay_task = accepted_tasks(replay)
    effects_after = fixture_evidence(scope)
    replay_matches = bool(replay_task) and set(replay_task).issubset(set(accepted))
    no_new_effect = effects_before.get("unique_external_effects") == effects_after.get(
        "unique_external_effects"
    )
    return {
        "submissions": [*submissions, replay],
        "accepted": accepted,
        "terminal": terminal,
        "trace_expected": accepted,
        "effect_expected": set(accepted),
        "no_effect_expected": set(),
        "assertions": [
            assertion(
                "E_all_unique_requests_accepted",
                len(accepted) == len(payloads),
                {"accepted": len(accepted), "expected": len(payloads)},
            ),
            assertion(
                "E_history_compacted_with_tombstone",
                sum(int(row["item_count"]) for row in compacted["history"]) > 0
                and int(compacted["idempotency"].get("tombstones", 0)) > 0,
                {
                    "history": compacted["history"],
                    "idempotency": compacted["idempotency"],
                },
            ),
            assertion(
                "E_restart_replay_same_task_no_new_effect",
                old_worker_pid != new_worker_pid and replay_matches and no_new_effect,
                {
                    "worker_replaced": old_worker_pid != new_worker_pid,
                    "replay_matches": replay_matches,
                    "effects_before": effects_before.get("unique_external_effects"),
                    "effects_after": effects_after.get("unique_external_effects"),
                },
            ),
        ],
        "input_sequence_sha256": payload_digest(payloads),
        "extra": {"compacted": compacted, "worker_replaced": old_worker_pid != new_worker_pid},
    }


def run_profile_f(
    scope: RuntimeScope,
    matrix: S2MatrixConfig,
    repetition: int,
) -> dict[str, Any]:
    payloads, traces = profile_payloads(
        profile_id="F",
        repetition=repetition,
        count=2,
        seed=matrix.seed + 500,
    )
    expired_submission = submit_payloads(
        api_url=scope.api.base_url,
        payloads=[payloads[0]],
        trace_seeds=[traces[0]],
        concurrency=1,
    )
    expired = accepted_tasks(expired_submission)
    if len(expired) != 1:
        raise RuntimeError("F_expiry_item_not_accepted")
    expired_task = next(iter(expired))
    expire_task(scope.database_url, scope.schema, expired_task)
    start_worker_and_monitoring(scope, matrix)
    expired_terminal = wait_for_terminal(
        scope,
        {expired_task},
        timeout=15,
        sample_interval=matrix.sample_interval_seconds,
    )
    healthy_submission = submit_payloads(
        api_url=scope.api.base_url,
        payloads=[payloads[1]],
        trace_seeds=[traces[1]],
        concurrency=1,
    )
    healthy = accepted_tasks(healthy_submission)
    terminal = wait_for_terminal(
        scope,
        set(expired) | set(healthy),
        timeout=matrix.drain_timeout_seconds,
        sample_interval=matrix.sample_interval_seconds,
    )
    expired_row = next(
        (row for row in terminal["final"]["queue"] if row["task_id"] == expired_task),
        {},
    )
    return {
        "submissions": [expired_submission, healthy_submission],
        "accepted": {**expired, **healthy},
        "terminal": terminal,
        "trace_expected": healthy,
        "effect_expected": set(healthy),
        "no_effect_expected": set(expired),
        "assertions": [
            assertion(
                "F_expired_without_effect",
                expired_terminal["closed"] and expired_row.get("state") == "expired",
                {
                    "state": expired_row.get("state"),
                    "terminal_reason": expired_row.get("terminal_reason"),
                },
            ),
            assertion(
                "F_following_healthy_completed",
                bool(healthy),
                {"healthy_accepted": len(healthy)},
            ),
        ],
        "input_sequence_sha256": payload_digest(payloads),
    }


def run_profile_g(
    scope: RuntimeScope,
    matrix: S2MatrixConfig,
    repetition: int,
) -> dict[str, Any]:
    spec = matrix.profiles["G"]
    transient_count = int(spec["transient_request_count"])
    healthy_count = int(spec["healthy_request_count"])
    modes = ["always_transient"] * transient_count + ["healthy"] * healthy_count
    payloads, traces = profile_payloads(
        profile_id="G",
        repetition=repetition,
        count=len(modes),
        seed=matrix.seed + 600,
        failure_modes=modes,
    )
    start_worker_and_monitoring(scope, matrix)
    submission = submit_payloads(
        api_url=scope.api.base_url,
        payloads=payloads,
        trace_seeds=traces,
        concurrency=int(spec["concurrency"]),
    )
    accepted = accepted_tasks(submission)
    mode_by_task: dict[str, str] = {}
    for item in submission["results"]:
        body = item.get("body", {})
        if item.get("status_code") == 202 and isinstance(body, Mapping):
            mode_by_task[str(body["task_id"])] = str(
                payloads[int(item["index"])]["config_payload"]["s2_failure_mode"]
            )
    terminal = wait_for_terminal(
        scope,
        set(accepted),
        timeout=matrix.drain_timeout_seconds,
        sample_interval=matrix.sample_interval_seconds,
    )
    healthy = {task for task, mode in mode_by_task.items() if mode == "healthy"}
    transient = set(accepted) - healthy
    reasons = Counter(
        str(row.get("terminal_reason"))
        for row in terminal["final"]["queue"]
        if row["task_id"] in transient
    )
    return {
        "submissions": [submission],
        "accepted": accepted,
        "terminal": terminal,
        "trace_expected": accepted,
        "effect_expected": healthy,
        "no_effect_expected": transient,
        "assertions": [
            assertion(
                "G_retry_budget_observed",
                bool(terminal["final"]["retry_budget"])
                and any("retry_budget_exhausted" in reason for reason in reasons),
                {"retry_budget": terminal["final"]["retry_budget"], "reasons": dict(reasons)},
            ),
            assertion(
                "G_healthy_completed_after_retry_pressure",
                len(healthy) == healthy_count,
                {"healthy": len(healthy), "expected": healthy_count},
            ),
        ],
        "input_sequence_sha256": payload_digest(payloads),
    }


def run_profile_h(
    scope: RuntimeScope,
    matrix: S2MatrixConfig,
    repetition: int,
) -> dict[str, Any]:
    spec = matrix.profiles["H"]
    poison_count = int(spec["poison_request_count"])
    healthy_count = int(spec["healthy_request_count"])
    modes = ["permanent"] * poison_count + ["healthy"] * healthy_count
    payloads, traces = profile_payloads(
        profile_id="H",
        repetition=repetition,
        count=len(modes),
        seed=matrix.seed + 700,
        failure_modes=modes,
    )
    start_worker_and_monitoring(scope, matrix)
    submission = submit_payloads(
        api_url=scope.api.base_url,
        payloads=payloads,
        trace_seeds=traces,
        concurrency=int(spec["concurrency"]),
    )
    accepted = accepted_tasks(submission)
    mode_by_task: dict[str, str] = {}
    for item in submission["results"]:
        body = item.get("body", {})
        if item.get("status_code") == 202 and isinstance(body, Mapping):
            mode_by_task[str(body["task_id"])] = str(
                payloads[int(item["index"])]["config_payload"]["s2_failure_mode"]
            )
    terminal = wait_for_terminal(
        scope,
        set(accepted),
        timeout=matrix.drain_timeout_seconds,
        sample_interval=matrix.sample_interval_seconds,
    )
    healthy = {task for task, mode in mode_by_task.items() if mode == "healthy"}
    poison = set(accepted) - healthy
    final_by_task = {str(row["task_id"]): row for row in terminal["final"]["queue"]}
    return {
        "submissions": [submission],
        "accepted": accepted,
        "terminal": terminal,
        "trace_expected": accepted,
        "effect_expected": healthy,
        "no_effect_expected": poison,
        "assertions": [
            assertion(
                "H_poison_quarantined",
                len(poison) == poison_count
                and all(final_by_task.get(task, {}).get("state") == "dlq" for task in poison),
                {"poison": len(poison), "states": [final_by_task.get(task, {}).get("state") for task in poison]},
            ),
            assertion(
                "H_healthy_not_head_of_line_blocked",
                len(healthy) == healthy_count
                and all(final_by_task.get(task, {}).get("state") == "completed" for task in healthy),
                {"healthy": len(healthy), "expected": healthy_count},
            ),
        ],
        "input_sequence_sha256": payload_digest(payloads),
    }


def wait_for_worker_in_flight(
    scope: RuntimeScope,
    *,
    timeout: float = 15.0,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    last: dict[str, Any] = {}
    while time.monotonic() < deadline:
        if scope.worker is None or scope.worker.process.poll() is not None:
            raise RuntimeError("worker_exited_before_in_flight_observation")
        if scope.worker_heartbeat and scope.worker_heartbeat.is_file():
            try:
                last = json.loads(scope.worker_heartbeat.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                last = {}
            in_flight = dict(last.get("in_flight", {}))
            if sum(int(value) for value in in_flight.values()) > 0:
                return last
        time.sleep(0.05)
    raise RuntimeError(f"worker_in_flight_timeout:{last}")


def run_profile_i(
    scope: RuntimeScope,
    matrix: S2MatrixConfig,
    repetition: int,
) -> dict[str, Any]:
    spec = matrix.profiles["I"]
    slow = build_task_payload(
        profile_id="I",
        repetition=repetition,
        index=0,
        idempotency_key=f"s2-i-r{repetition}-slow",
        delay_seconds=float(spec["worker_loss_delay_seconds"]),
        terminal_state="success",
        seed=matrix.seed + 800,
    )
    timeout_payload = build_task_payload(
        profile_id="I",
        repetition=repetition,
        index=1,
        idempotency_key=f"s2-i-r{repetition}-timeout",
        failure_mode="timeout_once",
        delay_seconds=float(spec["timeout_delay_seconds"]),
        terminal_state="success",
        seed=matrix.seed + 800,
    )
    healthy = build_task_payload(
        profile_id="I",
        repetition=repetition,
        index=2,
        idempotency_key=f"s2-i-r{repetition}-healthy",
        terminal_state="success",
        seed=matrix.seed + 800,
    )
    start_worker_and_monitoring(scope, matrix)
    slow_submission = submit_payloads(
        api_url=scope.api.base_url,
        payloads=[slow],
        trace_seeds=[f"s2:I:{repetition}:slow"],
        concurrency=1,
    )
    slow_accepted = accepted_tasks(slow_submission)
    if len(slow_accepted) != 1:
        raise RuntimeError("I_slow_item_not_accepted")
    slow_task = next(iter(slow_accepted))
    heartbeat = wait_for_worker_in_flight(scope)
    old_pid = scope.worker.pid if scope.worker else 0
    old_create_time = psutil.Process(old_pid).create_time()
    old_children = [child.pid for child in psutil.Process(old_pid).children(recursive=True)]
    stopped = scope.stop_worker(force=True)
    old_dead = not psutil.pid_exists(old_pid)
    orphan_children = [pid for pid in old_children if psutil.pid_exists(pid)]
    scope.start_worker()
    new_pid = scope.worker.pid if scope.worker else 0
    replacement_identity = {
        "old_pid": old_pid,
        "old_create_time": old_create_time,
        "new_pid": new_pid,
        "stopped_process_count": len(stopped),
        "old_dead": old_dead,
        "orphan_children": orphan_children,
    }
    slow_terminal = wait_for_terminal(
        scope,
        {slow_task},
        timeout=matrix.drain_timeout_seconds,
        sample_interval=matrix.sample_interval_seconds,
    )
    follow_submission = submit_payloads(
        api_url=scope.api.base_url,
        payloads=[timeout_payload, healthy],
        trace_seeds=[f"s2:I:{repetition}:timeout", f"s2:I:{repetition}:healthy"],
        concurrency=int(spec["concurrency"]),
        timeout=float(spec["timeout_delay_seconds"]) + 5,
    )
    follow_accepted = accepted_tasks(follow_submission)
    accepted = {**slow_accepted, **follow_accepted}
    terminal = wait_for_terminal(
        scope,
        set(accepted),
        timeout=matrix.drain_timeout_seconds,
        sample_interval=matrix.sample_interval_seconds,
    )
    rows = {str(row["task_id"]): row for row in terminal["final"]["queue"]}
    slow_epoch = int(rows.get(slow_task, {}).get("lease_epoch", 0))
    healthy_task = next(
        (
            str(item["body"]["task_id"])
            for item in follow_submission["results"]
            if item.get("status_code") == 202 and int(item["index"]) == 1
        ),
        "",
    )
    timeout_task = next(
        (
            str(item["body"]["task_id"])
            for item in follow_submission["results"]
            if item.get("status_code") == 202 and int(item["index"]) == 0
        ),
        "",
    )
    healthy_terminal_at = rows.get(healthy_task, {}).get("terminal_at")
    timeout_terminal_at = rows.get(timeout_task, {}).get("terminal_at")
    return {
        "submissions": [slow_submission, follow_submission],
        "accepted": accepted,
        "terminal": terminal,
        "trace_expected": accepted,
        "effect_expected": set(accepted),
        "no_effect_expected": set(),
        "assertions": [
            assertion(
                "I_exact_worker_process_replaced",
                old_pid != new_pid and old_dead and not orphan_children,
                {
                    "worker_identity_changed": old_pid != new_pid,
                    "old_process_dead": old_dead,
                    "stopped_process_count": len(stopped),
                    "orphan_child_count": len(orphan_children),
                },
            ),
            assertion(
                "I_lease_epoch_increased",
                slow_epoch >= 2,
                {"slow_task_lease_epoch": slow_epoch},
            ),
            assertion(
                "I_timeout_does_not_block_healthy",
                bool(healthy_terminal_at)
                and bool(timeout_terminal_at)
                and str(healthy_terminal_at) < str(timeout_terminal_at),
                {
                    "healthy_terminal_at": healthy_terminal_at,
                    "timeout_terminal_at": timeout_terminal_at,
                },
            ),
            assertion("I_slow_item_recovered", bool(slow_terminal["closed"]), slow_terminal["closed"]),
        ],
        "input_sequence_sha256": payload_digest([slow, timeout_payload, healthy]),
        "extra": {"worker_replacement": replacement_identity, "heartbeat": heartbeat},
    }


def run_profile_j(
    scope: RuntimeScope,
    matrix: S2MatrixConfig,
    repetition: int,
) -> dict[str, Any]:
    spec = matrix.profiles["J"]
    payloads, traces = profile_payloads(
        profile_id="J",
        repetition=repetition,
        count=int(spec["request_count"]),
        seed=matrix.seed + 900,
        resource_profile="windows-rtx-4080-super",
        terminal_after_seconds=float(spec["terminal_after_seconds"]),
        cuda_probe=True,
    )
    start_worker_and_monitoring(scope, matrix)
    submission = submit_payloads(
        api_url=scope.api.base_url,
        payloads=payloads,
        trace_seeds=traces,
        concurrency=int(spec["concurrency"]),
        timeout=30,
    )
    accepted = accepted_tasks(submission)
    terminal = wait_for_terminal(
        scope,
        set(accepted),
        timeout=matrix.drain_timeout_seconds,
        sample_interval=matrix.sample_interval_seconds,
    )
    fixture = fixture_evidence(scope)
    resources = {
        str(row.get("resource_class"))
        for row in terminal["final"]["queue"]
        if row.get("task_id") in accepted
    }
    return {
        "submissions": [submission],
        "accepted": accepted,
        "terminal": terminal,
        "trace_expected": accepted,
        "effect_expected": set(accepted),
        "no_effect_expected": set(),
        "assertions": [
            assertion(
                "J_existing_gpu_profile_routed_gpu",
                resources == {"gpu"},
                sorted(resources),
            ),
            assertion(
                "J_trusted_cuda_nonzero",
                int(fixture.get("cuda_probe_count", 0)) == len(accepted)
                and int(fixture.get("cuda_nonzero_activity_count", 0)) == len(accepted)
                and int(fixture.get("cuda_failure_count", -1)) == 0
                and int(fixture.get("cuda_peak_allocated_bytes", 0)) > 0,
                {
                    "accepted": len(accepted),
                    "probe_count": fixture.get("cuda_probe_count"),
                    "nonzero": fixture.get("cuda_nonzero_activity_count"),
                    "failures": fixture.get("cuda_failure_count"),
                    "peak_allocated_bytes": fixture.get("cuda_peak_allocated_bytes"),
                },
            ),
            assertion(
                "J_external_gpu_runtime_max_one",
                int(fixture.get("max_external_in_flight", {}).get("gpu", 0)) == 1
                and int(fixture.get("max_runtime_concurrency", {}).get("gpu", 0)) == 1,
                {
                    "external": fixture.get("max_external_in_flight", {}),
                    "runtime": fixture.get("max_runtime_concurrency", {}),
                },
            ),
        ],
        "input_sequence_sha256": payload_digest(payloads),
    }


PROFILE_RUNNERS = {
    "A": run_profile_a,
    "B": run_profile_b,
    "C": run_profile_c,
    "E": run_profile_e,
    "F": run_profile_f,
    "G": run_profile_g,
    "H": run_profile_h,
    "I": run_profile_i,
    "J": run_profile_j,
}


def run_profile_scope(
    *,
    root: Path,
    suite_root: Path,
    profile_root: Path,
    database_url: str,
    revision: str,
    branch: str,
    matrix: S2MatrixConfig,
    profile_id: str,
    repetition: int,
    queue_config_path: Path,
    trace_path: Path,
    variant: str | None = None,
) -> dict[str, Any]:
    suffix = f"{profile_id.lower()}{repetition}{variant or ''}".replace("-", "")
    schema = schema_identifier(f"s2_{revision[:8]}_{suffix}_{uuid4().hex[:6]}")
    marker = f"s2-{profile_id.lower()}-r{repetition}-{variant or 'main'}-{uuid4().hex[:5]}"
    private_root = suite_root / f"{profile_id}-r{repetition}-{variant or 'main'}"
    scope: RuntimeScope | None = None
    cleanup: dict[str, Any] = {
        "schema_dropped": False,
        "marker_processes_remaining": [],
        "errors": ["scope_not_started"],
    }
    try:
        scope = create_runtime_scope(
            root=root,
            private_root=private_root,
            profile_root=profile_root,
            database_url=database_url,
            schema=schema,
            revision=revision,
            branch=branch,
            queue_config_path=queue_config_path,
            trace_path=trace_path,
            marker=marker,
        )
        queue_config = AdmissionQueueConfig.from_path(queue_config_path)
        if profile_id == "D":
            result = run_profile_d_variant(
                scope,
                matrix,
                repetition,
                variant=str(variant),
            )
        else:
            result = PROFILE_RUNNERS[profile_id](scope, matrix, repetition)
        public, private = finalize_profile_scope(
            scope=scope,
            profile_id=profile_id,
            repetition=repetition,
            matrix=matrix,
            queue_config=queue_config,
            submissions=result["submissions"],
            accepted=result["accepted"],
            terminal=result["terminal"],
            trace_expected=result["trace_expected"],
            effect_expected=result["effect_expected"],
            no_effect_expected=result["no_effect_expected"],
            assertions=result["assertions"],
            input_sequence_sha256=result["input_sequence_sha256"],
            extra={**dict(result.get("extra", {})), "variant": variant},
        )
        if variant is not None:
            public["variant"] = variant
    except Exception as exc:
        private_root.mkdir(parents=True, exist_ok=True)
        failure = {
            "schema_version": "evm.s2_profile_failure.v1",
            "generated_at": utc_now(),
            "profile_id": profile_id,
            "repetition": repetition,
            "variant": variant,
            "failure": f"{type(exc).__name__}:{exc}",
            "accepted_for_closure": False,
        }
        private_path = private_root / "profile-result-private.json"
        canonical_write(private_path, failure)
        private = failure
        public = {
            "profile_id": profile_id,
            "name": matrix.profiles[profile_id]["name"],
            "repetition": repetition,
            "variant": variant,
            "assertions": [assertion("profile_execution", False, failure["failure"])],
            "private_evidence_sha256": sha256_file(private_path),
            "passed": False,
        }
    finally:
        if scope is not None:
            cleanup = scope.close()
    cleanup_passed = bool(cleanup.get("schema_dropped")) and not cleanup.get(
        "marker_processes_remaining"
    ) and not cleanup.get("errors")
    public["cleanup"] = cleanup
    public["assertions"] = [
        *list(public.get("assertions", [])),
        assertion("isolated_runtime_cleanup", cleanup_passed, cleanup),
    ]
    public["passed"] = bool(public.get("passed")) and cleanup_passed
    private["cleanup"] = cleanup
    private["passed"] = public["passed"]
    private_path = private_root / "profile-result-private.json"
    canonical_write(private_path, private)
    public["private_evidence_sha256"] = sha256_file(private_path)
    return public


def run_profile_d(
    *,
    root: Path,
    suite_root: Path,
    profile_root: Path,
    database_url: str,
    revision: str,
    branch: str,
    matrix: S2MatrixConfig,
    repetition: int,
    adaptive_config_path: Path,
    cpu1_config_path: Path,
    trace_path: Path,
) -> dict[str, Any]:
    adaptive = run_profile_scope(
        root=root,
        suite_root=suite_root,
        profile_root=profile_root,
        database_url=database_url,
        revision=revision,
        branch=branch,
        matrix=matrix,
        profile_id="D",
        repetition=repetition,
        queue_config_path=adaptive_config_path,
        trace_path=trace_path,
        variant="adaptive",
    )
    if not adaptive["passed"]:
        return {
            "profile_id": "D",
            "name": matrix.profiles["D"]["name"],
            "repetition": repetition,
            "variants": [adaptive],
            "assertions": [assertion("D_adaptive_variant", False, "adaptive failed")],
            "passed": False,
        }
    cpu1 = run_profile_scope(
        root=root,
        suite_root=suite_root,
        profile_root=profile_root,
        database_url=database_url,
        revision=revision,
        branch=branch,
        matrix=matrix,
        profile_id="D",
        repetition=repetition,
        queue_config_path=cpu1_config_path,
        trace_path=trace_path,
        variant="cpu1",
    )
    adaptive_scale_up = float(adaptive.get("metrics", {}).get("cpu_scale", {}).get("up", 0))
    cpu1_scale_up = float(cpu1.get("metrics", {}).get("cpu_scale", {}).get("up", 0))
    adaptive_runtime_concurrency = int(
        adaptive.get("external_effects", {}).get("max_runtime_concurrency", {}).get("cpu", 0)
    )
    cpu1_runtime_concurrency = int(
        cpu1.get("external_effects", {}).get("max_runtime_concurrency", {}).get("cpu", 0)
    )
    adaptive_throughput = float(
        adaptive.get("profile_observations", {}).get(
            "accepted_completion_throughput_per_second", 0
        )
    )
    cpu1_throughput = float(
        cpu1.get("profile_observations", {}).get(
            "accepted_completion_throughput_per_second", 0
        )
    )
    assertions = [
        assertion(
            "D_adaptive_cpu_scaled_non_vacuously",
            adaptive_scale_up >= 1
            and cpu1_scale_up == 0
            and adaptive_runtime_concurrency > 1
            and cpu1_runtime_concurrency == 1,
            {
                "adaptive_scale_up_events": adaptive_scale_up,
                "cpu1_scale_up_events": cpu1_scale_up,
                "adaptive_runtime_concurrency": adaptive_runtime_concurrency,
                "cpu1_runtime_concurrency": cpu1_runtime_concurrency,
            },
        ),
        assertion(
            "D_adaptive_throughput_exceeds_cpu1",
            adaptive_throughput > cpu1_throughput > 0,
            {
                "adaptive_accepted_throughput": adaptive_throughput,
                "cpu1_accepted_throughput": cpu1_throughput,
            },
        ),
    ]
    return {
        "profile_id": "D",
        "name": matrix.profiles["D"]["name"],
        "repetition": repetition,
        "variants": [adaptive, cpu1],
        "assertions": assertions,
        "passed": adaptive["passed"] and cpu1["passed"] and all(item["passed"] for item in assertions),
    }


def find_assertion(payload: Mapping[str, Any], assertion_id: str) -> bool:
    return any(
        item.get("assertion_id") == assertion_id and bool(item.get("passed"))
        for item in payload.get("assertions", [])
    )


def runtime_results(results: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    flattened: list[dict[str, Any]] = []
    for result in results:
        variants = result.get("variants")
        if isinstance(variants, list):
            flattened.extend(dict(item) for item in variants)
        else:
            flattened.append(dict(result))
    return flattened


def progress_verdict(scenario: Mapping[str, Any]) -> str | None:
    direct = scenario.get("verdict")
    if isinstance(direct, str):
        return direct
    boundary = scenario.get("verdict_and_claim_boundary")
    if isinstance(boundary, Mapping):
        nested = boundary.get("verdict")
        return nested if isinstance(nested, str) else None
    return None


def validate_start_gate(root: Path, progress_path: Path) -> dict[str, Any]:
    tracked_dirty = subprocess.check_output(
        ["git", "-C", str(root), "status", "--porcelain", "--untracked-files=no"],
        text=True,
    ).strip()
    if tracked_dirty:
        raise RuntimeError("tracked_worktree_not_clean")
    progress = json.loads(progress_path.read_text(encoding="utf-8"))
    scenarios = {item["scenario_id"]: item for item in progress.get("scenarios", [])}
    statuses = {
        scenario_id: {
            "status": scenarios.get(scenario_id, {}).get("status"),
            "verdict": progress_verdict(scenarios.get(scenario_id, {})),
        }
        for scenario_id in ("S0", "S1")
    }
    if any(
        value["status"] != "verified" or value["verdict"] != "passed"
        for value in statuses.values()
    ):
        raise RuntimeError(f"S0_S1_start_gate_failed:{statuses}")
    validator = subprocess.run(
        [
            sys.executable,
            str(root / "scripts" / "dev" / "validate_scale_scenario_progress.py"),
            "--progress",
            str(progress_path),
            "--markdown",
            str(progress_path.with_suffix(".md")),
            "--git-revision",
            "HEAD",
        ],
        cwd=root,
        capture_output=True,
        text=True,
    )
    if validator.returncode != 0:
        raise RuntimeError(
            "progress_git_blob_validator_failed:"
            + (validator.stderr.strip() or validator.stdout.strip())
        )
    return {
        "S0": statuses["S0"],
        "S1": statuses["S1"],
        "validator_passed": True,
        "validator_output_sha256": hashlib.sha256(
            validator.stdout.encode("utf-8")
        ).hexdigest(),
    }


def port_is_available(port: int) -> bool:
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        try:
            probe.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


def aggregate_acceptance(
    results: Sequence[Mapping[str, Any]],
    queue_config: AdmissionQueueConfig,
) -> tuple[dict[str, bool], dict[str, bool]]:
    flattened = runtime_results(results)
    hard_bounded = all(
        int(item.get("peaks", {}).get("active_depth", 0))
        <= queue_config.durable_max_depth
        and int(item.get("peaks", {}).get("active_bytes", 0))
        <= queue_config.durable_max_bytes
        and 0
        < int(item.get("peaks", {}).get("api_process_tree_rss_bytes", 0))
        <= queue_config.rss_cap_bytes
        and 0
        < int(item.get("peaks", {}).get("worker_process_tree_rss_bytes", 0))
        <= queue_config.rss_cap_bytes
        for item in flattened
    )
    counts = Counter(str(item.get("profile_id")) for item in results)
    exact_matrix = all(counts[profile_id] == 3 for profile_id in EXPECTED_PROFILE_IDS)
    d_entries = [item for item in flattened if item.get("profile_id") == "D"]
    slope_bounded = len(d_entries) == 6 and all(
        find_assertion(item, f"D_{item.get('variant')}_rss_slope_bounded")
        for item in d_entries
    )
    executor_entries = [
        item
        for item in flattened
        if item.get("profile_id") in {"B", "C", "D", "I", "J"}
    ]
    executor_observed = len(executor_entries) == 18 and all(
        int(item.get("peaks", {}).get("executor_children_rss_bytes", 0)) > 0
        for item in executor_entries
    )
    explicit_closure = all(
        find_assertion(item, "accepted_equals_terminal")
        and find_assertion(item, "active_queue_drained")
        and find_assertion(item, "outcome_unknown_zero")
        for item in flattened
    )
    exactly_once = all(
        find_assertion(item, "logical_effect_exactly_once") for item in flattened
    )
    poison_entries = [
        item for item in results if item.get("profile_id") in {"E", "H", "I", "J"}
    ]
    poison_isolated = len(poison_entries) == 12 and all(
        bool(item.get("passed"))
        for item in poison_entries
    )
    rejection_entries = [
        item for item in results if item.get("profile_id") in {"B", "C", "G", "H"}
    ]
    rejection_observed = len(rejection_entries) == 12 and all(
        bool(item.get("passed"))
        for item in rejection_entries
    )
    worker_loss_entries = [
        item for item in flattened if item.get("profile_id") == "I"
    ]
    gpu_entries = [item for item in flattened if item.get("profile_id") == "J"]
    acceptance = {
        "S2-AC-01": hard_bounded and slope_bounded and executor_observed,
        "S2-AC-02": explicit_closure,
        "S2-AC-03": exactly_once and poison_isolated,
        "S2-AC-04": rejection_observed,
    }
    readiness = {
        "RG-01-s0-s1-start-gate": True,
        "RG-02-exact-a-j-three-repetitions": exact_matrix and len(results) == 30,
        "RG-03-external-http-no-transport-loss": all(
            int(item.get("submission", {}).get("transport_failures", 0)) == 0
            for item in flattened
        ),
        "RG-04-real-postgresql-terminal-parity": explicit_closure
        and all(find_assertion(item, "postgres_json_mirror_parity") for item in flattened),
        "RG-05-real-worker-process-and-cleanup": all(
            find_assertion(item, "isolated_runtime_cleanup") for item in flattened
        ),
        "RG-06-prometheus-queryable": all(
            find_assertion(item, "prometheus_targets_up") for item in flattened
        ),
        "RG-07-per-task-otel-chain": all(
            find_assertion(item, "trace_chain_complete") for item in flattened
        ),
        "RG-08-retry-dlq-backpressure-observed": rejection_observed,
        "RG-09-real-worker-loss-recovery": len(worker_loss_entries) == 3 and all(
            find_assertion(item, "I_exact_worker_process_replaced")
            and find_assertion(item, "I_lease_epoch_increased")
            for item in worker_loss_entries
        ),
        "RG-10-trusted-cuda-bound-to-effect": len(gpu_entries) == 3 and all(
            find_assertion(item, "J_trusted_cuda_nonzero")
            and find_assertion(item, "J_external_gpu_runtime_max_one")
            for item in gpu_entries
        ),
    }
    return acceptance, readiness


def private_evidence_index(suite_root: Path) -> dict[str, Any]:
    artifacts = []
    for path in sorted(suite_root.rglob("*")):
        if not path.is_file():
            continue
        artifacts.append(
            {
                "path": path.relative_to(suite_root).as_posix(),
                "sha256": sha256_file(path),
                "bytes": path.stat().st_size,
            }
        )
    return {
        "schema_version": "evm.s2_private_evidence_index.v1",
        "generated_at": utc_now(),
        "artifacts": artifacts,
        "artifact_count": len(artifacts),
        "aggregate_sha256": canonical_digest(artifacts),
    }


def run_external_s2_experiment(args: argparse.Namespace) -> dict[str, Any]:
    root = args.root.resolve()
    revision, branch = source_revision(root)
    matrix = S2MatrixConfig.from_path(args.matrix)
    queue_config = AdmissionQueueConfig.from_path(args.queue_config)
    cpu1_config = AdmissionQueueConfig.from_path(args.cpu1_config)
    if cpu1_config.cpu_workers_min != 1 or cpu1_config.cpu_workers_max != 1:
        raise RuntimeError("S2 CPU1 control must freeze exactly one CPU worker")
    if cpu1_config.metrics_port != queue_config.metrics_port:
        raise RuntimeError("S2 adaptive and CPU1 configs must share the sequential metrics port")
    if not port_is_available(queue_config.metrics_port):
        raise RuntimeError(f"worker_metrics_port_in_use:{queue_config.metrics_port}")
    start_gate = validate_start_gate(root, args.progress)
    suite_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:6]
    suite_root = (args.private_root / suite_id).resolve()
    suite_root.mkdir(parents=True, exist_ok=False)
    isolated_profile = materialize_isolated_profile(
        source_root=args.profile_root,
        profile_id=args.profile_id,
        profile_version=args.profile_version,
        isolated_root=suite_root / "isolated-pipeline-profiles",
    )
    active_profile_root = Path(isolated_profile["profile_root"])
    selected_profiles = tuple(args.profiles)
    closure_eligible = selected_profiles == EXPECTED_PROFILE_IDS
    results: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    started_at = utc_now()
    for profile_id in selected_profiles:
        for repetition in range(1, matrix.repetitions + 1):
            if profile_id == "D":
                result = run_profile_d(
                    root=root,
                    suite_root=suite_root,
                    profile_root=active_profile_root,
                    database_url=args.database_url,
                    revision=revision,
                    branch=branch,
                    matrix=matrix,
                    repetition=repetition,
                    adaptive_config_path=args.queue_config,
                    cpu1_config_path=args.cpu1_config,
                    trace_path=args.trace_path,
                )
            else:
                result = run_profile_scope(
                    root=root,
                    suite_root=suite_root,
                    profile_root=active_profile_root,
                    database_url=args.database_url,
                    revision=revision,
                    branch=branch,
                    matrix=matrix,
                    profile_id=profile_id,
                    repetition=repetition,
                    queue_config_path=args.queue_config,
                    trace_path=args.trace_path,
                )
            results.append(result)
            canonical_write(suite_root / "suite-progress-private.json", {"results": results})
            if not result.get("passed"):
                failures.append(
                    {
                        "profile_id": profile_id,
                        "repetition": repetition,
                        "reason": "profile_acceptance_failed",
                    }
                )
                break
        if failures:
            break
    acceptance, readiness = aggregate_acceptance(results, queue_config)
    deterministic_inputs: dict[str, bool] = {}
    for profile_id in selected_profiles:
        profile_entries = [item for item in results if item.get("profile_id") == profile_id]
        if profile_id == "D":
            for variant in ("adaptive", "cpu1"):
                digests = {
                    str(candidate.get("input_sequence_sha256"))
                    for item in profile_entries
                    for candidate in item.get("variants", [])
                    if candidate.get("variant") == variant
                }
                deterministic_inputs[f"D-{variant}"] = len(digests) == 1 and "None" not in digests
        else:
            digests = {str(item.get("input_sequence_sha256")) for item in profile_entries}
            deterministic_inputs[profile_id] = len(digests) == 1 and "None" not in digests
    readiness["RG-11-fixed-seed-input-repeatability"] = all(deterministic_inputs.values())
    all_runtime_passed = bool(results) and all(bool(item.get("passed")) for item in results)
    runtime_passed = (
        closure_eligible
        and len(results) == len(EXPECTED_PROFILE_IDS) * matrix.repetitions
        and all_runtime_passed
        and all(acceptance.values())
        and all(readiness.values())
        and not failures
    )
    private_index = private_evidence_index(suite_root)
    canonical_write(suite_root / "private-evidence-index.json", private_index)
    public = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": utc_now(),
        "started_at": started_at,
        "source_identity": {"implementation_revision": revision, "branch": branch},
        "start_gate": start_gate,
        "matrix": {
            "version": matrix.version,
            "sha256": matrix.sha256,
            "seed": matrix.seed,
            "repetitions": matrix.repetitions,
            "profiles": list(selected_profiles),
            "warmup_seconds": matrix.warmup_seconds,
            "sample_interval_seconds": matrix.sample_interval_seconds,
            "rss_slope_measurement_seconds": matrix.rss_slope_measurement_seconds,
            "drain_timeout_seconds": matrix.drain_timeout_seconds,
        },
        "environment": {
            "database_engine": "PostgreSQL 16",
            "api_transport": "external_tcp_http",
            "worker_boundary": "real_dedicated_queue_worker_process",
            "dependency_boundary": "deterministic_airflow_compatible_http",
            "observability": "Prometheus_file_sd_and_W3C_OTLP",
            "gpu_boundary": "trusted_single_cuda_device_probe",
            "execution_scope": "isolated_schema_on_local_single_node",
            "customer_traffic": False,
            "pipeline_profile_digest": isolated_profile["profile_digest"],
            "pipeline_profile_reproducibility_digest": isolated_profile[
                "reproducibility_digest"
            ],
        },
        "profile_results": results,
        "deterministic_input_repeatability": deterministic_inputs,
        "acceptance": acceptance,
        "readiness_gates": readiness,
        "failed_attempts_and_rca": failures,
        "runtime_verdict": "passed" if runtime_passed else "failed",
        "scenario_status": (
            "exercised_pending_git_blob_closure" if runtime_passed else "implementing"
        ),
        "private_evidence": {
            "artifact_count": private_index["artifact_count"],
            "aggregate_sha256": private_index["aggregate_sha256"],
            "location": "outside_git_private_evidence_root",
        },
        "claim_boundary": CLAIM_BOUNDARY,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    write_public_json(args.output, public)
    canonical_write(suite_root / "suite-summary-private.json", public)
    return public


def parse_args(argv: Iterable[str] | None = None) -> argparse.Namespace:
    root = Path(__file__).resolve().parents[3]
    parser = argparse.ArgumentParser(
        description="Run S2 A-J bounded queue experiments through the existing external runtime."
    )
    parser.add_argument("--root", type=Path, default=root)
    parser.add_argument(
        "--output",
        type=Path,
        default=root / "docs" / "status" / "evidence" / "s2-bounded-queue-experiment.json",
    )
    parser.add_argument(
        "--private-root",
        type=Path,
        default=Path(
            "F:/EnterpriseMLOps_Data/enterprise-vision-mlops/artifacts/scale_validation/private/s2"
        ),
    )
    parser.add_argument(
        "--profile-root",
        type=Path,
        default=Path(
            "F:/EnterpriseMLOps_Data/enterprise-vision-mlops/artifacts/w7/pipeline_profiles"
        ),
    )
    parser.add_argument("--profile-id", default="standard-b0-manual-tuning")
    parser.add_argument("--profile-version", type=int, default=11)
    parser.add_argument(
        "--database-url",
        default=os.getenv(
            "EVM_CONTROL_PLANE_DATABASE_URL",
            "postgresql://evm_control_plane:evm_control_plane_local@127.0.0.1:5434/evm_control_plane",
        ),
    )
    parser.add_argument(
        "--matrix",
        type=Path,
        default=root / "configs" / "s2_experiment_matrix_v1.toml",
    )
    parser.add_argument(
        "--queue-config",
        type=Path,
        default=root / "configs" / "s2_bounded_queue_v1.toml",
    )
    parser.add_argument(
        "--cpu1-config",
        type=Path,
        default=root / "configs" / "s2_bounded_queue_cpu1_control.toml",
    )
    parser.add_argument(
        "--progress",
        type=Path,
        default=root / "docs" / "status" / "2026-08-15-distributed-scale-scenario-progress.json",
    )
    parser.add_argument(
        "--trace-path",
        type=Path,
        default=Path(
            os.getenv(
                "EVM_S2_OTEL_TRACE_PATH",
                "F:/EnterpriseMLOps_Data/enterprise-vision-mlops/artifacts/scale_validation/otel/traces.json",
            )
        ),
    )
    parser.add_argument("--profiles", nargs="+", default=list(EXPECTED_PROFILE_IDS))
    args = parser.parse_args(argv)
    invalid = [profile for profile in args.profiles if profile not in EXPECTED_PROFILE_IDS]
    if invalid:
        raise SystemExit(f"unsupported S2 profiles: {invalid}")
    if len(set(args.profiles)) != len(args.profiles):
        raise SystemExit("S2 profile selection cannot contain duplicates")
    if not args.profile_root.is_dir():
        raise SystemExit(f"pipeline profile root is missing: {args.profile_root}")
    return args


def main(argv: Iterable[str] | None = None) -> int:
    args = parse_args(argv)
    result = run_external_s2_experiment(args)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["runtime_verdict"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
