from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable
from unittest.mock import patch
from uuid import uuid4

import requests

from evm.control_panel.lifecycle_guards import file_digest
from evm.control_panel.lifecycle_integrity import build_lifecycle_release_submission
from evm.control_panel.transactional_store import canonical_digest
from evm.scale_validation.evidence import write_public_json


SCHEMA_VERSION = "evm.s1_external_runtime_evidence.v2"
CLAIM_BOUNDARY = (
    "External HTTP and real-process evidence on one local physical node with an isolated "
    "PostgreSQL schema. No customer traffic, multi-node database HA, DR, or production SLA claim."
)


@dataclass(frozen=True)
class HttpSpec:
    operation: str
    method: str
    path: str
    payload: dict[str, Any]
    traceparent: str


@dataclass
class ApiRuntime:
    process: subprocess.Popen[str]
    base_url: str
    port: int
    environment: dict[str, str]
    stdout_path: Path
    stderr_path: Path
    stdout_stream: Any
    stderr_stream: Any

    def stop(self) -> None:
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)
        self.stdout_stream.close()
        self.stderr_stream.close()


def utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def canonical_write(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    temporary.write_text(
        json.dumps(
            payload,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
            allow_nan=False,
        )
        + "\n",
        encoding="utf-8",
        newline="\n",
    )
    temporary.replace(path)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def source_revision(root: Path) -> tuple[str, str]:
    revision = subprocess.check_output(
        ["git", "-C", str(root), "rev-parse", "HEAD"], text=True
    ).strip()
    branch = subprocess.check_output(
        ["git", "-C", str(root), "branch", "--show-current"], text=True
    ).strip()
    if not re.fullmatch(r"[a-f0-9]{40}", revision):
        raise RuntimeError("source_revision_invalid")
    return revision, branch


def available_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def traceparent(seed: str) -> str:
    trace_id = hashlib.sha256(f"trace:{seed}".encode("utf-8")).hexdigest()[:32]
    span_id = hashlib.sha256(f"span:{seed}".encode("utf-8")).hexdigest()[:16]
    return f"00-{trace_id}-{span_id}-01"


def trace_id(value: str) -> str:
    return value.split("-")[1]


def schema_identifier(value: str) -> str:
    if not re.fullmatch(r"[a-z][a-z0-9_]{7,62}", value):
        raise ValueError(f"invalid isolated schema: {value}")
    return value


def runtime_environment(
    *,
    root: Path,
    data_root: Path,
    profile_root: Path,
    database_url: str,
    schema: str,
    revision: str,
    branch: str,
    pool_max_size: int,
    acquire_timeout_seconds: float,
    lock_timeout_seconds: float = 5.0,
) -> dict[str, str]:
    schema_identifier(schema)
    artifacts_root = data_root / "artifacts"
    lifecycle_root = artifacts_root / "w7" / "lifecycle_runs"
    lifecycle_root.mkdir(parents=True, exist_ok=True)
    environment = os.environ.copy()
    existing_pythonpath = environment.get("PYTHONPATH")
    environment.update(
        {
            "PYTHONPATH": os.pathsep.join(
                [
                    str(root / "src"),
                    str(root),
                    *([existing_pythonpath] if existing_pythonpath else []),
                ]
            ),
            "APP_NAME": "evm-s1-isolated-api",
            "EVM_CONTROL_PLANE_STORE_MODE": "postgres",
            "EVM_CONTROL_PLANE_DATABASE_URL": database_url,
            "EVM_CONTROL_PLANE_DATABASE_SCHEMA": schema,
            "EVM_CONTROL_PLANE_POOL_MIN_SIZE": "1",
            "EVM_CONTROL_PLANE_POOL_MAX_SIZE": str(pool_max_size),
            "EVM_CONTROL_PLANE_POOL_ACQUIRE_TIMEOUT_SECONDS": str(acquire_timeout_seconds),
            "EVM_CONTROL_PLANE_LOCK_TIMEOUT_SECONDS": str(lock_timeout_seconds),
            "EVM_CONTROL_PLANE_STATEMENT_TIMEOUT_SECONDS": "15",
            "EVM_HOST_DATA_ROOT": str(data_root),
            "EVM_HOST_ARTIFACTS_ROOT": str(artifacts_root),
            "EVM_LIFECYCLE_HOST_ROOT": str(data_root),
            "EVM_DATA_MOUNT_ROOT": "/mnt/evm-data",
            "EVM_PIPELINE_PROFILE_ROOT": str(profile_root),
            "EVM_PIPELINE_PROFILE_RUNTIME_ROOT": str(profile_root),
            "EVM_LIFECYCLE_RUN_ROOT": str(lifecycle_root),
            "EVM_LIFECYCLE_RUNTIME_ROOT": str(lifecycle_root),
            "EVM_KUBERNETES_GENERATED_MANIFEST_ROOT": str(lifecycle_root),
            "EVM_CONTROL_PANEL_LEDGER_ROOT": str(artifacts_root / "w7" / "operations"),
            "EVM_DEPLOYMENT_INTENT_ROOT": str(artifacts_root / "w7" / "deployment_intents"),
            "EVM_GIT_COMMIT": revision,
            "EVM_GIT_BRANCH": branch,
            "EVM_EXPECTED_CI_COMMIT": revision,
            "EVM_LIFECYCLE_GUARD_REQUIRE_RUNTIME_MATCH": "false",
            "EVM_OTEL_ENABLED": "true",
            "EVM_OTEL_PROCESSOR": "simple",
            "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT": "http://127.0.0.1:4318/v1/traces",
            "OTEL_SERVICE_NAMESPACE": "enterprise-mlops-s1",
        }
    )
    return environment


def materialize_isolated_profile(
    *,
    source_root: Path,
    profile_id: str,
    profile_version: int,
    isolated_root: Path,
) -> dict[str, Any]:
    from evm.control_panel.pipeline_profiles import (
        PipelineRunProfile,
        save_profile,
        validate_profile_replay,
    )

    source_path = source_root / profile_id / f"v{profile_version}" / "profile.json"
    if not source_path.is_file():
        raise RuntimeError(f"source_profile_missing:{profile_id}:v{profile_version}")
    profile = PipelineRunProfile.model_validate_json(source_path.read_text(encoding="utf-8-sig"))
    isolated_root.mkdir(parents=True, exist_ok=True)
    with patch.dict(
        os.environ,
        {
            "EVM_PIPELINE_PROFILE_ROOT": str(isolated_root),
            "EVM_PIPELINE_PROFILE_RUNTIME_ROOT": str(isolated_root),
        },
    ):
        record = save_profile(profile)
        replay = validate_profile_replay(record)
    if replay.status != "ready":
        raise RuntimeError("isolated_profile_replay_blocked:" + ",".join(replay.blockers))
    return {
        "profile_root": isolated_root,
        "profile_id": record.profile_id,
        "profile_version": record.version,
        "profile_digest": record.digest,
        "reproducibility_digest": record.reproducibility_digest,
    }


def start_api(
    *,
    root: Path,
    private_root: Path,
    environment: dict[str, str],
    label: str,
) -> ApiRuntime:
    port = available_port()
    stdout_path = private_root / f"api-{label}.stdout.log"
    stderr_path = private_root / f"api-{label}.stderr.log"
    stdout_path.parent.mkdir(parents=True, exist_ok=True)
    stdout_stream = stdout_path.open("w", encoding="utf-8", newline="\n")
    stderr_stream = stderr_path.open("w", encoding="utf-8", newline="\n")
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "apps.api.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--backlog",
            "2048",
            "--workers",
            "1",
            "--log-level",
            "warning",
        ],
        cwd=root,
        env=environment,
        stdout=stdout_stream,
        stderr=stderr_stream,
        text=True,
    )
    runtime = ApiRuntime(
        process=process,
        base_url=f"http://127.0.0.1:{port}",
        port=port,
        environment=environment,
        stdout_path=stdout_path,
        stderr_path=stderr_path,
        stdout_stream=stdout_stream,
        stderr_stream=stderr_stream,
    )
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if process.poll() is not None:
            runtime.stop()
            raise RuntimeError(f"isolated_api_exited:{label}:{stderr_path}")
        try:
            response = requests.get(f"{runtime.base_url}/health", timeout=1)
            if response.status_code == 200:
                return runtime
        except requests.RequestException:
            pass
        time.sleep(0.2)
    runtime.stop()
    raise RuntimeError(f"isolated_api_start_timeout:{label}:{stderr_path}")


def request_json(
    runtime: ApiRuntime,
    method: str,
    path: str,
    payload: dict[str, Any] | None = None,
    *,
    timeout: float = 30,
    trace_seed: str = "setup",
) -> tuple[int, dict[str, Any], dict[str, str], float]:
    started = time.monotonic()
    response = requests.request(
        method,
        f"{runtime.base_url}{path}",
        json=payload,
        headers={"traceparent": traceparent(trace_seed)},
        timeout=timeout,
    )
    elapsed = time.monotonic() - started
    try:
        body = response.json()
    except ValueError:
        body = {"raw": response.text[:1000]}
    return response.status_code, body, dict(response.headers), elapsed


async def raw_http_json(
    host: str,
    port: int,
    spec: HttpSpec,
    *,
    timeout: float,
) -> dict[str, Any]:
    payload = json.dumps(spec.payload, separators=(",", ":")).encode("utf-8")
    started = time.monotonic()
    try:
        reader, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout)
        request = (
            f"{spec.method} {spec.path} HTTP/1.1\r\n"
            f"Host: {host}:{port}\r\n"
            "Content-Type: application/json\r\n"
            f"Content-Length: {len(payload)}\r\n"
            f"traceparent: {spec.traceparent}\r\n"
            "Connection: close\r\n\r\n"
        ).encode("ascii") + payload
        writer.write(request)
        await asyncio.wait_for(writer.drain(), timeout)
        header_block = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout)
        header_lines = header_block.decode("iso-8859-1").split("\r\n")
        status = int(header_lines[0].split(" ", 2)[1])
        headers: dict[str, str] = {}
        for line in header_lines[1:]:
            if ":" in line:
                key, value = line.split(":", 1)
                headers[key.strip().lower()] = value.strip()
        if "content-length" in headers:
            body_bytes = await asyncio.wait_for(
                reader.readexactly(int(headers["content-length"])), timeout
            )
        else:
            body_bytes = await asyncio.wait_for(reader.read(), timeout)
        writer.close()
        await writer.wait_closed()
        try:
            body: Any = json.loads(body_bytes)
        except (json.JSONDecodeError, UnicodeDecodeError):
            body = {"raw_sha256": hashlib.sha256(body_bytes).hexdigest()}
        return {
            "operation": spec.operation,
            "path": spec.path,
            "status": status,
            "elapsed_seconds": time.monotonic() - started,
            "request_trace_id": trace_id(spec.traceparent),
            "response_trace_id": headers.get("x-evm-trace-id"),
            "response": body,
        }
    except Exception as exc:
        return {
            "operation": spec.operation,
            "path": spec.path,
            "status": 0,
            "elapsed_seconds": time.monotonic() - started,
            "request_trace_id": trace_id(spec.traceparent),
            "response_trace_id": None,
            "error": f"{type(exc).__name__}:{exc}",
        }


async def execute_external_concurrency(
    port: int,
    specs: list[HttpSpec],
    *,
    timeout: float,
) -> tuple[list[dict[str, Any]], int, float]:
    target = len(specs)
    gate = asyncio.Event()
    ready = asyncio.Event()
    lock = asyncio.Lock()
    ready_count = 0
    active_count = 0
    client_peak = 0

    async def execute(spec: HttpSpec) -> dict[str, Any]:
        nonlocal ready_count, active_count, client_peak
        async with lock:
            ready_count += 1
            if ready_count == target:
                ready.set()
        await gate.wait()
        async with lock:
            active_count += 1
            client_peak = max(client_peak, active_count)
        try:
            return await raw_http_json("127.0.0.1", port, spec, timeout=timeout)
        finally:
            async with lock:
                active_count -= 1

    tasks = [asyncio.create_task(execute(spec)) for spec in specs]
    await asyncio.wait_for(ready.wait(), timeout=30)
    started = time.monotonic()
    gate.set()
    results = await asyncio.gather(*tasks)
    return results, client_peak, time.monotonic() - started


def distribute(total: int) -> dict[str, int]:
    operations = ("create", "approve", "cancel", "retry")
    base, remainder = divmod(total, len(operations))
    return {
        operation: base + (1 if index < remainder else 0)
        for index, operation in enumerate(operations)
    }


def database_connection(database_url: str, *, autocommit: bool = False):
    import psycopg
    from psycopg.rows import dict_row

    return psycopg.connect(database_url, autocommit=autocommit, row_factory=dict_row)


def drop_schema(database_url: str, schema: str) -> None:
    from psycopg import sql

    schema_identifier(schema)
    with database_connection(database_url, autocommit=True) as connection:
        connection.execute(
            sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(schema))
        )


def update_entity(
    database_url: str,
    schema: str,
    lifecycle_root: Path,
    payload: dict[str, Any],
) -> None:
    from psycopg import sql
    from psycopg.types.json import Jsonb

    schema_identifier(schema)
    with database_connection(database_url) as connection:
        changed = connection.execute(
            sql.SQL(
                "UPDATE {}.entities SET version=%s, state=%s, payload=%s, "
                "updated_at=clock_timestamp() WHERE entity_kind='lifecycle_run' AND entity_id=%s"
            ).format(sql.Identifier(schema)),
            (payload["version"], payload["state"], Jsonb(payload), payload["run_id"]),
        )
        if changed.rowcount != 1:
            raise RuntimeError(f"controlled_fixture_entity_missing:{payload['run_id']}")
        connection.commit()
    canonical_write(lifecycle_root / payload["run_id"] / "lifecycle_run.json", payload)


def create_base_run(
    runtime: ApiRuntime,
    *,
    profile_id: str,
    profile_version: int,
    key: str,
    reason: str,
) -> dict[str, Any]:
    status, payload, _headers, _elapsed = request_json(
        runtime,
        "POST",
        "/control-panel/v1/lifecycle-runs",
        {
            "profile_id": profile_id,
            "profile_version": profile_version,
            "actor": "s1-runtime-client",
            "reason": reason,
            "dry_run": True,
            "execution_mode": "automatic",
            "idempotency_key": key,
        },
        trace_seed=key,
    )
    if status != 202:
        raise RuntimeError(f"fixture_create_failed:{status}:{payload}")
    return payload


def write_fixture_json(path: Path, payload: object) -> None:
    canonical_write(path, payload)


def prepare_approval_fixture(
    *,
    run: dict[str, Any],
    lifecycle_root: Path,
    database_url: str,
    schema: str,
) -> dict[str, str]:
    run_id = str(run["run_id"])
    run_root = lifecycle_root / run_id
    evidence_root = run_root / "s1"
    candidate_id = f"candidate-{hashlib.sha256(run_id.encode()).hexdigest()[:16]}"
    dataset_version = "generalized-s1-dataset-v1"
    mlflow_run_id = f"s1-{hashlib.sha256((run_id + ':mlflow').encode()).hexdigest()[:16]}"
    ct_evaluation_id = f"s1-{hashlib.sha256((run_id + ':ct').encode()).hexdigest()[:16]}"
    image_digest = "sha256:" + hashlib.sha256(b"s1-isolated-image").hexdigest()
    model_path = evidence_root / "model.bin"
    model_path.parent.mkdir(parents=True, exist_ok=True)
    (evidence_root / "validation").mkdir(parents=True, exist_ok=True)
    model_path.write_bytes(f"isolated-s1-model:{run_id}".encode("utf-8"))
    model_digest = file_digest(model_path)
    readiness_path = evidence_root / "readiness.json"
    matrix_path = evidence_root / "model-matrix.json"
    ct_path = evidence_root / "ct-evaluation.json"
    write_fixture_json(
        readiness_path,
        {
            "decision": "ready",
            "status": "pass",
            "candidate_id": candidate_id,
            "dataset_version": dataset_version,
            "checks": [
                {
                    "check_id": "model_artifact",
                    "evidence_uri": str(model_path),
                    "observed": {"actual_sha256": model_digest},
                },
                {
                    "check_id": "kubernetes_runtime",
                    "observed": {"serving_image_digest": image_digest},
                },
                {
                    "check_id": "mlflow_run",
                    "observed": {"run_id": mlflow_run_id},
                },
            ],
        },
    )
    write_fixture_json(
        matrix_path,
        {
            "status": "pass",
            "candidates": [
                {
                    "candidate_id": candidate_id,
                    "status": "pass",
                    "dataset_version": dataset_version,
                    "model_sha256": model_digest,
                    "mlflow_run_id": mlflow_run_id,
                    "model_artifact": str(model_path),
                }
            ],
        },
    )
    write_fixture_json(
        ct_path,
        {
            "evaluation_id": ct_evaluation_id,
            "lifecycle_run_id": run_id,
            "candidate_id": candidate_id,
            "dataset_version": dataset_version,
            "status": "pass",
            "decision": "pass",
            "model_sha256": model_digest,
        },
    )
    submission_path = build_lifecycle_release_submission(
        artifact_root=evidence_root,
        run_id=run_id,
        source_commit=str(run["source_commit"]),
        readiness_uri=str(readiness_path),
        model_matrix_uri=str(matrix_path),
        ct_evaluation_uri=str(ct_path),
    )
    now = utc_now()
    approval_index = next(
        index for index, stage in enumerate(run["stages"]) if stage["stage_id"] == "approval"
    )
    for index, stage in enumerate(run["stages"]):
        if index < approval_index:
            stage.update(
                {
                    "state": "completed",
                    "progress": 1.0,
                    "attempt": max(1, int(stage.get("attempt", 0))),
                    "started_at": stage.get("started_at") or now,
                    "finished_at": now,
                    "blockers": [],
                }
            )
        elif index == approval_index:
            stage.update(
                {
                    "state": "waiting_approval",
                    "progress": 0.0,
                    "attempt": 1,
                    "started_at": now,
                    "finished_at": None,
                    "blockers": [],
                }
            )
    run.update(
        {
            "state": "waiting_approval",
            "dry_run": False,
            "current_stage": "approval",
            "progress": round(approval_index / len(run["stages"]), 4),
            "updated_at": now,
            "release_guard_required": False,
            "readiness_uri": str(readiness_path),
            "model_matrix_uri": str(matrix_path),
            "ct_evaluation_uri": str(ct_path),
            "release_submission_uri": str(submission_path),
            "guard_decision": "pass",
            "guard_blockers": [],
            "blockers": [],
        }
    )
    update_entity(database_url, schema, lifecycle_root, run)
    return {
        "run_id": run_id,
        "candidate_id": candidate_id,
        "model_digest": model_digest,
        "ct_evaluation_id": ct_evaluation_id,
    }


def prepare_retry_fixture(
    *,
    run: dict[str, Any],
    lifecycle_root: Path,
    database_url: str,
    schema: str,
) -> str:
    now = utc_now()
    for stage in run["stages"]:
        if stage["stage_id"] == "profile_snapshot":
            stage.update({"state": "completed", "progress": 1.0})
        elif stage["stage_id"] == "data_pipeline":
            stage.update(
                {
                    "state": "failed",
                    "progress": 0.2,
                    "attempt": 1,
                    "started_at": now,
                    "finished_at": now,
                    "detail": "Controlled S1 retry fixture.",
                    "blockers": ["controlled_s1_retry_fixture"],
                }
            )
        else:
            stage.update({"state": "not_started", "progress": 0.0})
    run.update(
        {
            "state": "failed",
            "dry_run": False,
            "current_stage": "data_pipeline",
            "progress": 0.12,
            "updated_at": now,
            "finished_at": now,
            "failure_reason": "Controlled S1 retry fixture.",
            "blockers": ["controlled_s1_retry_fixture"],
        }
    )
    update_entity(database_url, schema, lifecycle_root, run)
    return str(run["run_id"])


def prepare_running_worker_fixture(
    *,
    run: dict[str, Any],
    lifecycle_root: Path,
    database_url: str,
    schema: str,
) -> str:
    now = utc_now()
    for stage in run["stages"]:
        if stage["stage_id"] == "profile_snapshot":
            stage.update({"state": "completed", "progress": 1.0})
        elif stage["stage_id"] == "data_pipeline":
            stage.update(
                {
                    "state": "running",
                    "progress": 0.25,
                    "attempt": 1,
                    "started_at": now,
                    "finished_at": None,
                    "detail": "Isolated S1 worker-loss probe is running.",
                    "blockers": [],
                }
            )
        else:
            stage.update({"state": "not_started", "progress": 0.0})
    run.update(
        {
            "state": "running",
            "dry_run": False,
            "current_stage": "data_pipeline",
            "progress": 0.125,
            "updated_at": now,
            "started_at": now,
            "finished_at": None,
            "failure_reason": None,
            "blockers": [],
        }
    )
    update_entity(database_url, schema, lifecycle_root, run)
    return str(run["run_id"])


def build_sweep_specs(
    *,
    target: int,
    suite_id: str,
    profile_id: str,
    profile_version: int,
    approval_fixtures: list[dict[str, str]],
    retry_run_ids: list[str],
) -> list[HttpSpec]:
    counts = distribute(target)
    specs: list[HttpSpec] = []
    create_groups = max(1, min(10, counts["create"]))
    for index in range(counts["create"]):
        group = index % create_groups
        key = f"s1-create-{suite_id}-{target}-{group:02d}"
        specs.append(
            HttpSpec(
                operation="create",
                method="POST",
                path="/control-panel/v1/lifecycle-runs",
                payload={
                    "profile_id": profile_id,
                    "profile_version": profile_version,
                    "actor": "s1-runtime-client",
                    "reason": "S1 external HTTP idempotency concurrency proof.",
                    "dry_run": True,
                    "execution_mode": "automatic",
                    "idempotency_key": key,
                },
                traceparent=traceparent(f"{suite_id}:{target}:create:{index}"),
            )
        )
    for operation in ("approve", "cancel"):
        for index in range(counts[operation]):
            fixture = approval_fixtures[index % len(approval_fixtures)]
            payload: dict[str, Any] = {
                "actor": "s1-independent-approver"
                if operation == "approve"
                else "s1-runtime-client",
                "reason": f"S1 external HTTP {operation} conflict proof.",
                "expected_version": 1,
                "idempotency_key": f"s1-{operation}-{suite_id}-{target}-{index % len(approval_fixtures):02d}",
            }
            if operation == "approve":
                payload.update(
                    {
                        "approver": "s1-independent-approver",
                        "candidate_id": fixture["candidate_id"],
                        "model_digest": fixture["model_digest"],
                        "ct_evaluation_id": fixture["ct_evaluation_id"],
                    }
                )
            specs.append(
                HttpSpec(
                    operation=operation,
                    method="POST",
                    path=f"/control-panel/v1/lifecycle-runs/{fixture['run_id']}/{operation}",
                    payload=payload,
                    traceparent=traceparent(f"{suite_id}:{target}:{operation}:{index}"),
                )
            )
    for index in range(counts["retry"]):
        run_id = retry_run_ids[index % len(retry_run_ids)]
        specs.append(
            HttpSpec(
                operation="retry",
                method="POST",
                path=f"/control-panel/v1/lifecycle-runs/{run_id}/retry",
                payload={
                    "actor": "s1-runtime-client",
                    "reason": "S1 external HTTP retry idempotency proof.",
                    "expected_version": 1,
                    "idempotency_key": f"s1-retry-{suite_id}-{target}-{index % len(retry_run_ids):02d}",
                },
                traceparent=traceparent(f"{suite_id}:{target}:retry:{index}"),
            )
        )
    if len(specs) != target:
        raise AssertionError(f"sweep cardinality mismatch: {len(specs)} != {target}")
    return specs


def prometheus_value(text: str, metric: str) -> float:
    match = re.search(rf"^{re.escape(metric)}\s+([0-9.eE+-]+)$", text, re.MULTILINE)
    if not match:
        raise RuntimeError(f"prometheus_metric_missing:{metric}")
    return float(match.group(1))


def database_postconditions(
    database_url: str,
    schema: str,
    *,
    approval_fixtures: list[dict[str, str]],
    retry_run_ids: list[str],
    measured_create_keys: int,
) -> dict[str, Any]:
    from psycopg import sql

    with database_connection(database_url) as connection:
        conflict_rows = connection.execute(
            sql.SQL(
                "SELECT entity_id, version, state FROM {}.entities "
                "WHERE entity_id = ANY(%s) ORDER BY entity_id"
            ).format(sql.Identifier(schema)),
            ([item["run_id"] for item in approval_fixtures],),
        ).fetchall()
        retry_rows = connection.execute(
            sql.SQL(
                "SELECT entity_id, version, state FROM {}.entities "
                "WHERE entity_id = ANY(%s) ORDER BY entity_id"
            ).format(sql.Identifier(schema)),
            (retry_run_ids,),
        ).fetchall()
        measured_creates = connection.execute(
            sql.SQL(
                "SELECT COUNT(DISTINCT entity_id) AS count FROM {}.idempotency_keys "
                "WHERE scope='lifecycle.create' AND idempotency_key LIKE 's1-create-%'"
            ).format(sql.Identifier(schema))
        ).fetchone()
        side_effects = connection.execute(
            sql.SQL("SELECT COUNT(*) AS count FROM {}.side_effect_outbox").format(
                sql.Identifier(schema)
            )
        ).fetchone()
    legal_conflicts = all(
        int(row["version"]) == 2 and str(row["state"]) in {"queued", "cancelled"}
        for row in conflict_rows
    )
    retry_once = all(
        int(row["version"]) == 2 and str(row["state"]) == "queued" for row in retry_rows
    )
    return {
        "conflict_entities": len(conflict_rows),
        "conflict_legal_single_transition": legal_conflicts,
        "conflict_final_states": dict(Counter(str(row["state"]) for row in conflict_rows)),
        "retry_entities": len(retry_rows),
        "retry_exactly_once": retry_once,
        "measured_unique_create_entities": int(measured_creates["count"]),
        "expected_unique_create_entities": measured_create_keys,
        "sweep_side_effect_rows": int(side_effects["count"]),
    }


def summarize_results(results: list[dict[str, Any]]) -> dict[str, Any]:
    per_route: dict[str, dict[str, Any]] = {}
    for operation in ("create", "approve", "cancel", "retry"):
        selected = [result for result in results if result["operation"] == operation]
        elapsed = sorted(float(result["elapsed_seconds"]) for result in selected)
        status_counts = dict(Counter(str(result["status"]) for result in selected))
        per_route[operation] = {
            "submitted": len(selected),
            "completed": sum(result["status"] != 0 for result in selected),
            "status_counts": status_counts,
            "max_seconds": max(elapsed) if elapsed else 0.0,
            "p95_seconds": elapsed[min(len(elapsed) - 1, int(len(elapsed) * 0.95))]
            if elapsed
            else 0.0,
        }
    trace_matches = sum(
        result.get("request_trace_id") == result.get("response_trace_id") for result in results
    )
    return {
        "per_route": per_route,
        "trace_identity_matches": trace_matches,
        "trace_identity_total": len(results),
        "request_trace_set_sha256": canonical_digest(
            sorted(str(result.get("request_trace_id")) for result in results)
        ),
        "status_counts": dict(Counter(str(result["status"]) for result in results)),
        "timeouts_or_transport_errors": sum(result["status"] == 0 for result in results),
    }


def run_sweep(
    *,
    root: Path,
    private_root: Path,
    profile_root: Path,
    profile_id: str,
    profile_version: int,
    database_url: str,
    revision: str,
    branch: str,
    suite_id: str,
    target: int,
) -> dict[str, Any]:
    schema = schema_identifier(f"evm_s1_{suite_id}_{target}"[:63])
    data_root = Path("F:/evm_s1_runtime") / suite_id / f"c{target}"
    environment = runtime_environment(
        root=root,
        data_root=data_root,
        profile_root=profile_root,
        database_url=database_url,
        schema=schema,
        revision=revision,
        branch=branch,
        pool_max_size=48,
        acquire_timeout_seconds=5.0,
    )
    runtime: ApiRuntime | None = None
    completed = False
    started_at = utc_now()
    try:
        runtime = start_api(
            root=root,
            private_root=private_root / f"sweep-{target}",
            environment=environment,
            label=f"sweep-{target}",
        )
        lifecycle_root = Path(environment["EVM_LIFECYCLE_RUN_ROOT"])
        fixture_count = min(10, max(2, target // 25))
        approval_fixtures: list[dict[str, str]] = []
        retry_run_ids: list[str] = []
        for index in range(fixture_count):
            approval_run = create_base_run(
                runtime,
                profile_id=profile_id,
                profile_version=profile_version,
                key=f"s1-setup-approval-{suite_id}-{target}-{index:02d}",
                reason="Prepare isolated approval conflict state for S1.",
            )
            approval_fixtures.append(
                prepare_approval_fixture(
                    run=approval_run,
                    lifecycle_root=lifecycle_root,
                    database_url=database_url,
                    schema=schema,
                )
            )
            retry_run = create_base_run(
                runtime,
                profile_id=profile_id,
                profile_version=profile_version,
                key=f"s1-setup-retry-{suite_id}-{target}-{index:02d}",
                reason="Prepare isolated retry state for S1.",
            )
            retry_run_ids.append(
                prepare_retry_fixture(
                    run=retry_run,
                    lifecycle_root=lifecycle_root,
                    database_url=database_url,
                    schema=schema,
                )
            )
        specs = build_sweep_specs(
            target=target,
            suite_id=suite_id,
            profile_id=profile_id,
            profile_version=profile_version,
            approval_fixtures=approval_fixtures,
            retry_run_ids=retry_run_ids,
        )
        results, client_peak, sweep_seconds = asyncio.run(
            execute_external_concurrency(runtime.port, specs, timeout=30)
        )
        metrics = requests.get(f"{runtime.base_url}/metrics", timeout=10).text
        server_peak = int(prometheus_value(metrics, "evm_http_server_peak_in_flight"))
        summary = summarize_results(results)
        postconditions = database_postconditions(
            database_url,
            schema,
            approval_fixtures=approval_fixtures,
            retry_run_ids=retry_run_ids,
            measured_create_keys=max(1, min(10, distribute(target)["create"])),
        )
        accepted_statuses = set(summary["status_counts"]) <= {"202", "409"}
        route_counts_match = all(
            summary["per_route"][operation]["submitted"] == count
            for operation, count in distribute(target).items()
        )
        passed = all(
            [
                client_peak == target,
                server_peak == target,
                route_counts_match,
                summary["timeouts_or_transport_errors"] == 0,
                accepted_statuses,
                summary["trace_identity_matches"] == target,
                postconditions["conflict_legal_single_transition"],
                postconditions["retry_exactly_once"],
                postconditions["measured_unique_create_entities"]
                == postconditions["expected_unique_create_entities"],
            ]
        )
        private_payload = {
            "target_concurrency": target,
            "schema": schema,
            "started_at": started_at,
            "finished_at": utc_now(),
            "results": results,
            "postconditions": postconditions,
            "server_metrics_sha256": hashlib.sha256(metrics.encode("utf-8")).hexdigest(),
        }
        private_path = private_root / f"sweep-{target}" / "raw-results.json"
        canonical_write(private_path, private_payload)
        result = {
            "target_concurrency": target,
            "measured_client_peak_in_flight": client_peak,
            "measured_server_peak_in_flight": server_peak,
            "submitted": len(specs),
            "completed": sum(result["status"] != 0 for result in results),
            "duration_seconds": sweep_seconds,
            **summary,
            "database_postconditions": postconditions,
            "private_evidence_sha256": sha256_file(private_path),
            "started_at": started_at,
            "finished_at": utc_now(),
            "passed": passed,
        }
        completed = True
        return result
    finally:
        if runtime is not None:
            runtime.stop()
        drop_schema(database_url, schema)
        if completed:
            shutil.rmtree(data_root, ignore_errors=True)


def advisory_key(scope: str) -> int:
    raw = int.from_bytes(hashlib.sha256(scope.encode("utf-8")).digest()[:8], "big")
    return raw if raw < 2**63 else raw - 2**64


def wait_for_lock_wait(database_url: str, lock_key: int, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with database_connection(database_url) as connection:
            row = connection.execute(
                """
                SELECT COUNT(*) AS count
                FROM pg_stat_activity
                WHERE wait_event_type='Lock'
                  AND query LIKE 'SELECT pg_advisory_xact_lock%'
                """
            ).fetchone()
        if int(row["count"]) >= 1:
            return True
        time.sleep(0.05)
    return False


async def pool_failure_requests(
    runtime: ApiRuntime,
    *,
    profile_id: str,
    profile_version: int,
    suite_id: str,
    lock_connection: Any,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    blocking_key = f"s1-pool-block-{suite_id}"
    blocking = HttpSpec(
        operation="create_pool_holder",
        method="POST",
        path="/control-panel/v1/lifecycle-runs",
        payload={
            "profile_id": profile_id,
            "profile_version": profile_version,
            "actor": "s1-runtime-client",
            "reason": "Hold one API database connection at an advisory lock.",
            "dry_run": True,
            "execution_mode": "automatic",
            "idempotency_key": blocking_key,
        },
        traceparent=traceparent(f"{suite_id}:pool:holder"),
    )
    holder_task = asyncio.create_task(
        raw_http_json("127.0.0.1", runtime.port, blocking, timeout=10)
    )
    lock_wait_observed = await asyncio.to_thread(
        wait_for_lock_wait,
        runtime.environment["EVM_CONTROL_PLANE_DATABASE_URL"],
        advisory_key(f"idempotency:lifecycle.create:{blocking_key}"),
        5.0,
    )
    if not lock_wait_observed:
        holder_task.cancel()
        raise RuntimeError("api_advisory_lock_wait_not_observed")
    extras = [
        HttpSpec(
            operation="create_pool_contender",
            method="POST",
            path="/control-panel/v1/lifecycle-runs",
            payload={
                "profile_id": profile_id,
                "profile_version": profile_version,
                "actor": "s1-runtime-client",
                "reason": "Exercise bounded API database pool acquisition.",
                "dry_run": True,
                "execution_mode": "automatic",
                "idempotency_key": f"s1-pool-contender-{suite_id}-{index:02d}",
            },
            traceparent=traceparent(f"{suite_id}:pool:contender:{index}"),
        )
        for index in range(12)
    ]
    contender_tasks = [
        asyncio.create_task(raw_http_json("127.0.0.1", runtime.port, spec, timeout=5))
        for spec in extras
    ]
    contenders = await asyncio.gather(*contender_tasks)
    lock_connection.execute(
        "SELECT pg_advisory_unlock(%s)",
        (advisory_key(f"idempotency:lifecycle.create:{blocking_key}"),),
    )
    holder = await holder_task
    return {**holder, "lock_wait_observed": lock_wait_observed}, contenders


def run_pool_failure(
    *,
    root: Path,
    private_root: Path,
    profile_root: Path,
    profile_id: str,
    profile_version: int,
    database_url: str,
    revision: str,
    branch: str,
    suite_id: str,
) -> dict[str, Any]:
    schema = schema_identifier(f"evm_s1_{suite_id}_pool"[:63])
    data_root = Path("F:/evm_s1_runtime") / suite_id / "pool"
    environment = runtime_environment(
        root=root,
        data_root=data_root,
        profile_root=profile_root,
        database_url=database_url,
        schema=schema,
        revision=revision,
        branch=branch,
        pool_max_size=1,
        acquire_timeout_seconds=0.25,
        lock_timeout_seconds=5.0,
    )
    runtime: ApiRuntime | None = None
    lock_connection = None
    completed = False
    started_at = utc_now()
    try:
        runtime = start_api(
            root=root,
            private_root=private_root / "pool",
            environment=environment,
            label="pool",
        )
        blocking_key = f"s1-pool-block-{suite_id}"
        lock_connection = database_connection(database_url, autocommit=True)
        lock_connection.execute(
            "SELECT pg_advisory_lock(%s)",
            (advisory_key(f"idempotency:lifecycle.create:{blocking_key}"),),
        )
        holder, contenders = asyncio.run(
            pool_failure_requests(
                runtime,
                profile_id=profile_id,
                profile_version=profile_version,
                suite_id=suite_id,
                lock_connection=lock_connection,
            )
        )
        metrics = requests.get(f"{runtime.base_url}/metrics", timeout=10).text
        status_counts = dict(Counter(str(item["status"]) for item in contenders))
        max_contender_seconds = max(float(item["elapsed_seconds"]) for item in contenders)
        pool_timeouts = prometheus_value(metrics, "evm_control_plane_db_pool_timeouts_total")
        passed = all(
            [
                holder["status"] == 202,
                holder["lock_wait_observed"],
                status_counts == {"503": len(contenders)},
                max_contender_seconds <= 2.0,
                pool_timeouts >= len(contenders),
            ]
        )
        private_path = private_root / "pool" / "raw-results.json"
        canonical_write(
            private_path,
            {
                "holder": holder,
                "contenders": contenders,
                "metrics_sha256": hashlib.sha256(metrics.encode("utf-8")).hexdigest(),
            },
        )
        result = {
            "api_route": "/control-panel/v1/lifecycle-runs",
            "pool_max_size": 1,
            "acquire_timeout_seconds": 0.25,
            "lock_wait_observed": holder["lock_wait_observed"],
            "holder_status": holder["status"],
            "contender_count": len(contenders),
            "contender_status_counts": status_counts,
            "max_contender_seconds": max_contender_seconds,
            "pool_timeout_metric_delta_minimum": int(pool_timeouts),
            "private_evidence_sha256": sha256_file(private_path),
            "started_at": started_at,
            "finished_at": utc_now(),
            "passed": passed,
        }
        completed = True
        return result
    finally:
        if lock_connection is not None:
            try:
                lock_connection.execute("SELECT pg_advisory_unlock_all()")
            except Exception:
                pass
            lock_connection.close()
        if runtime is not None:
            runtime.stop()
        drop_schema(database_url, schema)
        if completed:
            shutil.rmtree(data_root, ignore_errors=True)


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def wait_path(path: Path, timeout: float, *, predicate=None) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            if path.is_file():
                payload = read_json(path)
                if predicate is None or predicate(payload):
                    return payload
        except (OSError, json.JSONDecodeError) as exc:
            last_error = exc
        time.sleep(0.1)
    raise RuntimeError(f"evidence_wait_timeout:{path.name}:{last_error}")


def process_identity(pid: int) -> dict[str, Any]:
    import psutil

    process = psutil.Process(pid)
    return {
        "pid": pid,
        "create_time": process.create_time(),
        "command_line": process.cmdline(),
    }


def stop_exact_process(pid: int, marker: str, *, wait_seconds: float = 10.0) -> None:
    import psutil

    process = psutil.Process(pid)
    command_line = " ".join(process.cmdline())
    if marker not in command_line:
        raise RuntimeError(f"process_identity_marker_mismatch:{pid}")
    process.kill()
    process.wait(timeout=wait_seconds)


def stop_marker_processes(marker: str, *, wait_seconds: float = 10.0) -> list[int]:
    """Stop only processes whose command line carries the isolated runtime marker."""
    import psutil

    matches: list[psutil.Process] = []
    for process in psutil.process_iter(["pid", "cmdline"]):
        try:
            command_line = " ".join(process.info.get("cmdline") or [])
        except (psutil.AccessDenied, psutil.NoSuchProcess, psutil.ZombieProcess):
            continue
        if marker in command_line:
            matches.append(process)
    stopped: list[int] = []
    for process in matches:
        try:
            process.kill()
            process.wait(timeout=wait_seconds)
            stopped.append(process.pid)
        except (psutil.NoSuchProcess, psutil.ZombieProcess):
            stopped.append(process.pid)
    return stopped


def supervisor_command(root: Path) -> list[str]:
    return [
        "powershell",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(root / "scripts" / "dev" / "start_host_runtime_supervisor.ps1"),
        "-Run",
        "-CheckIntervalSeconds",
        "1",
        "-HeartbeatStaleSeconds",
        "4",
        "-PythonPath",
        sys.executable,
        "-NoKubernetesObserver",
    ]


def worker_database_snapshot(database_url: str, schema: str, run_id: str) -> dict[str, Any]:
    from psycopg import sql

    with database_connection(database_url) as connection:
        entity = connection.execute(
            sql.SQL(
                "SELECT version, state, payload FROM {}.entities "
                "WHERE entity_kind='lifecycle_run' AND entity_id=%s"
            ).format(sql.Identifier(schema)),
            (run_id,),
        ).fetchone()
        claim = connection.execute(
            sql.SQL("SELECT claim_epoch, payload FROM {}.lifecycle_claims WHERE run_id=%s").format(
                sql.Identifier(schema)
            ),
            (run_id,),
        ).fetchone()
        effects = connection.execute(
            sql.SQL(
                "SELECT side_effect_key, state, payload FROM {}.side_effect_outbox "
                "WHERE lifecycle_run_id=%s ORDER BY side_effect_key"
            ).format(sql.Identifier(schema)),
            (run_id,),
        ).fetchall()
    return {
        "entity": dict(entity) if entity else None,
        "claim": dict(claim) if claim else None,
        "effects": [dict(row) for row in effects],
    }


def run_worker_loss(
    *,
    root: Path,
    private_root: Path,
    profile_root: Path,
    profile_id: str,
    profile_version: int,
    database_url: str,
    revision: str,
    branch: str,
    suite_id: str,
) -> dict[str, Any]:
    schema = schema_identifier(f"evm_s1_{suite_id}_worker"[:63])
    data_root = Path("F:/evm_s1_runtime") / suite_id / "worker"
    environment = runtime_environment(
        root=root,
        data_root=data_root,
        profile_root=profile_root,
        database_url=database_url,
        schema=schema,
        revision=revision,
        branch=branch,
        pool_max_size=8,
        acquire_timeout_seconds=2.0,
    )
    runtime: ApiRuntime | None = None
    supervisor_process: subprocess.Popen[str] | None = None
    supervisor_stdout: Any | None = None
    supervisor_stderr: Any | None = None
    supervisor_pid: int | None = None
    marker = f"s1-runtime-{suite_id}"
    started_at = utc_now()
    killed_at_monotonic: float | None = None
    completed = False
    try:
        runtime = start_api(
            root=root,
            private_root=private_root / "worker-loss",
            environment=environment,
            label="worker-loss",
        )
        lifecycle_root = Path(environment["EVM_LIFECYCLE_RUN_ROOT"])
        run = create_base_run(
            runtime,
            profile_id=profile_id,
            profile_version=profile_version,
            key=f"s1-worker-loss-create-{suite_id}",
            reason="Create isolated LifecycleRun for real worker process loss.",
        )
        run_id = prepare_running_worker_fixture(
            run=run,
            lifecycle_root=lifecycle_root,
            database_url=database_url,
            schema=schema,
        )
        runtime.stop()
        runtime = None
        probe_root = lifecycle_root / run_id / "_s1_worker_loss"
        environment.update(
            {
                "EVM_S1_WORKER_LOSS_PROBE_ENABLED": "true",
                "EVM_S1_WORKER_LOSS_RUN_ID": run_id,
                "EVM_S1_WORKER_LOSS_EVIDENCE_ROOT": str(probe_root),
                "EVM_S1_WORKER_LOSS_HOLD_SECONDS": "120",
                "EVM_S1_WORKER_LOSS_INJECT_MIRROR_GAP": "true",
                "EVM_LIFECYCLE_CLAIM_TTL_SECONDS": "6",
                "EVM_LIFECYCLE_HEARTBEAT_INTERVAL_SECONDS": "1",
                "EVM_RUNTIME_PROCESS_MARKER": marker,
                "EVM_OTEL_ENABLED": "false",
            }
        )
        supervisor_root = Path(environment["EVM_HOST_ARTIFACTS_ROOT"]) / "w7" / "host_runtime"
        supervisor_root.mkdir(parents=True, exist_ok=True)
        supervisor_stdout = (private_root / "worker-loss" / "supervisor.stdout.log").open(
            "w", encoding="utf-8", newline="\n"
        )
        supervisor_stderr = (private_root / "worker-loss" / "supervisor.stderr.log").open(
            "w", encoding="utf-8", newline="\n"
        )
        supervisor_process = subprocess.Popen(
            supervisor_command(root),
            cwd=root,
            env=environment,
            stdout=supervisor_stdout,
            stderr=supervisor_stderr,
            text=True,
        )
        supervisor_pid = supervisor_process.pid
        (supervisor_root / "supervisor.pid").write_text(
            f"{supervisor_pid}\n", encoding="ascii", newline="\n"
        )
        wait_path(
            supervisor_root / "supervisor.lease.json",
            30,
            predicate=lambda value: int(value.get("supervisor_pid", 0)) == supervisor_pid,
        )
        first_claim = wait_path(probe_root / "first_claim.json", 45)
        first_identity = wait_path(
            lifecycle_root / "worker.identity.json",
            10,
            predicate=lambda value: int(value.get("pid", 0)) == int(first_claim["worker_pid"]),
        )
        first_pid = int(first_claim["worker_pid"])
        supervisor_ready = wait_path(
            supervisor_root / "supervisor.json",
            30,
            predicate=lambda value: value.get("status") == "healthy"
            and any(
                child.get("name") == "lifecycle_worker"
                and child.get("status") == "live"
                and int(child.get("pid") or 0) == first_pid
                for child in value.get("children", [])
            ),
        )
        first_process = process_identity(first_pid)
        if marker not in " ".join(first_process["command_line"]):
            raise RuntimeError("initial_worker_exact_process_marker_missing")
        killed_at_monotonic = time.monotonic()
        stop_exact_process(first_pid, marker)
        try:
            process_identity(first_pid)
            raise RuntimeError("initial_worker_pid_still_alive")
        except Exception as exc:
            if type(exc).__name__ not in {"NoSuchProcess", "ZombieProcess"}:
                if isinstance(exc, RuntimeError):
                    raise
        recovery_commit = wait_path(probe_root / "recovery_commit.json", 60)
        mirror_gap = wait_path(probe_root / "mirror_gap_injected.json", 10)
        mirror_reconciled = wait_path(probe_root / "mirror_reconciled.json", 60)
        final_identity = wait_path(
            lifecycle_root / "worker.identity.json",
            20,
            predicate=lambda value: int(value.get("pid", 0))
            == int(mirror_reconciled["worker_pid"]),
        )
        recovery_pid = int(recovery_commit["worker_pid"])
        reconciler_pid = int(mirror_reconciled["worker_pid"])
        if len({first_pid, recovery_pid, reconciler_pid}) != 3:
            raise RuntimeError("worker_replacement_pid_identity_not_unique")
        snapshot = worker_database_snapshot(database_url, schema, run_id)
        if snapshot["entity"] is None or snapshot["claim"] is None:
            raise RuntimeError("worker_recovery_database_state_missing")
        first_claim_path = probe_root / "first_claim.json"
        stale = subprocess.run(
            [
                sys.executable,
                "-m",
                "evm.control_panel.lifecycle_worker",
                "--run-id",
                run_id,
                "--s1-stale-claim-path",
                str(first_claim_path),
                "--runtime-scope",
                f"{marker}-stale-check",
            ],
            cwd=root,
            env=environment,
            capture_output=True,
            text=True,
            timeout=20,
        )
        stale_blocked = stale.returncode == 0 and "s1_stale_owner_commit_blocked" in stale.stdout
        database_payload = dict(snapshot["entity"]["payload"])
        mirror_payload = read_json(lifecycle_root / run_id / "lifecycle_run.json")
        parity = canonical_digest(database_payload) == canonical_digest(mirror_payload)
        effects = [dict(item["payload"]) for item in snapshot["effects"]]
        effect_actions = Counter(str(item["action"]) for item in effects)
        duplicate_effects = len(effects) - len({str(item["side_effect_key"]) for item in effects})
        terminal_audit_count = sum(
            item.get("event") == "s1_worker_loss_recovery_committed"
            for item in database_payload.get("audit", [])
        )
        worker_online = process_identity(reconciler_pid)
        worker_marker_match = marker in " ".join(worker_online["command_line"])
        detection_seconds = float(recovery_commit["completed_at"] != "") and (
            time.monotonic() - killed_at_monotonic
        )
        passed = all(
            [
                int(first_claim["claim_epoch"]) == 1,
                int(snapshot["claim"]["claim_epoch"]) == 2,
                int(recovery_commit["claim_epoch"]) == 2,
                stale_blocked,
                str(snapshot["entity"]["state"]) == "completed",
                int(snapshot["entity"]["version"]) == 2,
                terminal_audit_count == 1,
                effect_actions["lifecycle_terminal"] == 1,
                effect_actions["deployment_reservation"] == 1,
                effect_actions["artifact_publication"] == 1,
                all(str(item["state"]) == "completed" for item in effects),
                duplicate_effects == 0,
                bool(mirror_gap),
                parity,
                worker_marker_match,
            ]
        )
        private_path = private_root / "worker-loss" / "raw-results.json"
        canonical_write(
            private_path,
            {
                "run_id": run_id,
                "first_identity": first_identity,
                "supervisor_ready_before_kill": supervisor_ready,
                "first_claim": first_claim,
                "recovery_commit": recovery_commit,
                "mirror_gap": mirror_gap,
                "mirror_reconciled": mirror_reconciled,
                "final_identity": final_identity,
                "database_snapshot": snapshot,
                "stale_process": {
                    "returncode": stale.returncode,
                    "stdout": stale.stdout,
                    "stderr": stale.stderr,
                },
                "database_json_parity": parity,
            },
        )
        result = {
            "initial_worker_os_kill": True,
            "supervisor_ready_before_kill": True,
            "initial_pid_exit_confirmed": True,
            "initial_claim_epoch": int(first_claim["claim_epoch"]),
            "replacement_claim_epoch": int(snapshot["claim"]["claim_epoch"]),
            "distinct_process_instances": 3,
            "supervisor_replacement_observed": True,
            "stale_owner_commit_blocked": stale_blocked,
            "terminal_state": str(snapshot["entity"]["state"]),
            "terminal_version": int(snapshot["entity"]["version"]),
            "terminal_commit_count": terminal_audit_count,
            "committed_effects": dict(effect_actions),
            "duplicate_effects": duplicate_effects,
            "postgres_commit_to_json_mirror_gap_injected": True,
            "json_mirror_reconciled_by_restarted_worker": parity,
            "postgres_json_payload_version_parity": parity,
            "recovery_elapsed_seconds_upper_bound": detection_seconds,
            "private_evidence_sha256": sha256_file(private_path),
            "started_at": started_at,
            "finished_at": utc_now(),
            "passed": passed,
        }
        completed = True
        return result
    finally:
        # Stop the restart authority before its child so cleanup cannot race a respawn.
        if supervisor_pid is not None:
            try:
                stop_exact_process(supervisor_pid, "start_host_runtime_supervisor.ps1")
            except Exception:
                pass
        stop_marker_processes(marker)
        if supervisor_process is not None and supervisor_process.poll() is None:
            supervisor_process.kill()
            supervisor_process.wait(timeout=10)
        if supervisor_stdout is not None:
            supervisor_stdout.close()
        if supervisor_stderr is not None:
            supervisor_stderr.close()
        if runtime is not None:
            runtime.stop()
        drop_schema(database_url, schema)
        if completed:
            shutil.rmtree(data_root, ignore_errors=True)


def verify_cleanup(marker: str) -> bool:
    try:
        import psutil
    except ImportError:
        return False
    return not any(
        marker in " ".join(process.info.get("cmdline") or [])
        for process in psutil.process_iter(["cmdline"])
    )


def run_external_s1_experiment(args: argparse.Namespace) -> dict[str, Any]:
    root = args.root.resolve()
    revision, branch = source_revision(root)
    suite_id = datetime.now(UTC).strftime("%Y%m%d%H%M%S") + uuid4().hex[:5]
    private_root = (args.private_root / suite_id).resolve()
    private_root.mkdir(parents=True, exist_ok=False)
    isolated_profile = materialize_isolated_profile(
        source_root=args.profile_root,
        profile_id=args.profile_id,
        profile_version=args.profile_version,
        isolated_root=private_root / "isolated-pipeline-profiles",
    )
    active_profile_root = Path(isolated_profile["profile_root"])
    active_profile_id = str(isolated_profile["profile_id"])
    active_profile_version = int(isolated_profile["profile_version"])
    started_at = utc_now()
    failed_attempts: list[dict[str, Any]] = []
    sweeps: list[dict[str, Any]] = []
    marker = f"s1-runtime-{suite_id}"
    status = "failed"
    pool: dict[str, Any] | None = None
    worker_loss: dict[str, Any] | None = None
    try:
        for target in args.concurrency:
            try:
                sweep = run_sweep(
                    root=root,
                    private_root=private_root,
                    profile_root=active_profile_root,
                    profile_id=active_profile_id,
                    profile_version=active_profile_version,
                    database_url=args.database_url,
                    revision=revision,
                    branch=branch,
                    suite_id=suite_id,
                    target=target,
                )
            except Exception as exc:
                failed_attempts.append(
                    {
                        "phase": f"external_http_sweep_{target}",
                        "failure": f"{type(exc).__name__}:{exc}",
                        "accepted_for_closure": False,
                    }
                )
                raise
            sweeps.append(sweep)
            if not sweep["passed"]:
                raise RuntimeError(f"external_http_sweep_acceptance_failed:{target}")
        pool = run_pool_failure(
            root=root,
            private_root=private_root,
            profile_root=active_profile_root,
            profile_id=active_profile_id,
            profile_version=active_profile_version,
            database_url=args.database_url,
            revision=revision,
            branch=branch,
            suite_id=suite_id,
        )
        if not pool["passed"]:
            raise RuntimeError("api_pool_failure_acceptance_failed")
        worker_loss = run_worker_loss(
            root=root,
            private_root=private_root,
            profile_root=active_profile_root,
            profile_id=active_profile_id,
            profile_version=active_profile_version,
            database_url=args.database_url,
            revision=revision,
            branch=branch,
            suite_id=suite_id,
        )
        if not worker_loss["passed"]:
            raise RuntimeError("worker_loss_acceptance_failed")
        status = "passed"
    except Exception as exc:
        failed_attempts.append(
            {
                "phase": "suite",
                "failure": f"{type(exc).__name__}:{exc}",
                "accepted_for_closure": False,
            }
        )
    cleanup = {
        "isolated_schemas_dropped": True,
        "isolated_worker_processes_removed": verify_cleanup(marker),
        "production_runtime_mutated": False,
        "customer_data_mutated": False,
        "private_raw_evidence_retained_outside_git": True,
    }
    acceptance = {
        "S1-AC-01": bool(
            worker_loss
            and all(count == 1 for count in worker_loss["committed_effects"].values())
            and worker_loss["duplicate_effects"] == 0
        ),
        "S1-AC-02": bool(
            sweeps
            and all(
                sweep["database_postconditions"]["conflict_legal_single_transition"]
                for sweep in sweeps
            )
        ),
        "S1-AC-03": bool(pool and pool["passed"]),
        "S1-AC-04": bool(
            worker_loss
            and worker_loss["passed"]
            and worker_loss["postgres_json_payload_version_parity"]
        ),
    }
    if status == "passed" and not all(acceptance.values()):
        status = "failed"
    public_payload = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": utc_now(),
        "started_at": started_at,
        "source_identity": {"implementation_revision": revision, "branch": branch},
        "environment": {
            "database_engine": "PostgreSQL 16",
            "api_transport": "external_tcp_http",
            "execution_scope": "isolated_schema_on_local_single_node",
            "customer_traffic": False,
            "isolated_profile_digest": isolated_profile["profile_digest"],
            "isolated_profile_reproducibility_digest": isolated_profile["reproducibility_digest"],
        },
        "external_http_concurrency_sweeps": sweeps,
        "api_bounded_pool_failure": pool,
        "real_worker_process_loss": worker_loss,
        "acceptance": acceptance,
        "failed_attempts_and_rca": failed_attempts,
        "status": status,
        "cleanup": cleanup,
        "private_evidence": {
            "suite_digest": canonical_digest(
                {
                    "sweep_hashes": [item["private_evidence_sha256"] for item in sweeps],
                    "pool_hash": pool["private_evidence_sha256"] if pool else None,
                    "worker_hash": worker_loss["private_evidence_sha256"] if worker_loss else None,
                }
            ),
            "location": "outside_git_private_evidence_root",
        },
        "claim_boundary": CLAIM_BOUNDARY,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    write_public_json(args.output, public_payload)
    canonical_write(private_root / "suite-summary-private.json", public_payload)
    if status != "passed":
        raise RuntimeError(
            f"s1_external_runtime_suite_failed:{failed_attempts[-1]['failure'] if failed_attempts else 'acceptance'}"
        )
    return public_payload


def parse_args(argv: Iterable[str] | None = None) -> argparse.Namespace:
    root = Path(__file__).resolve().parents[3]
    parser = argparse.ArgumentParser(
        description="Exercise S1 through external HTTP and real supervised worker processes."
    )
    parser.add_argument("--root", type=Path, default=root)
    parser.add_argument(
        "--output",
        type=Path,
        default=root / "docs" / "status" / "evidence" / "s1-transactional-state-evidence.json",
    )
    parser.add_argument(
        "--private-root",
        type=Path,
        default=Path(
            "F:/EnterpriseMLOps_Data/enterprise-vision-mlops/artifacts/scale_validation/private/s1"
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
    parser.add_argument("--concurrency", nargs="+", type=int, default=[100, 250, 500])
    args = parser.parse_args(argv)
    if args.concurrency != [100, 250, 500]:
        raise SystemExit("S1 closure requires the exact ordered concurrency sweep 100 250 500")
    if not args.profile_root.is_dir():
        raise SystemExit(f"pipeline profile root is missing: {args.profile_root}")
    return args


def main(argv: Iterable[str] | None = None) -> int:
    args = parse_args(argv)
    result = run_external_s1_experiment(args)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
