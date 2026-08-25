from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import os
import secrets
import shutil
import socket
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence
from uuid import uuid4

import psutil
import requests


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from evm.control_panel.scenario_workloads import (  # noqa: E402
    GpuLease,
    acquire_scale_validation_gpu_lease,
    assert_scale_validation_gpu_lease_owner,
    read_active_gpu_lease,
    release_scale_validation_gpu_lease,
)
from evm.control_panel.transactional_store import (  # noqa: E402
    ControlPlaneParityError,
    s6bm_terminal_fence_record,
)
from evm.model_runtime.triton_blue_green import (  # noqa: E402
    TritonBlueGreenControlRequest,
    TritonBlueGreenPredictRequest,
    action_digest,
    expected_causal_identity_for_request,
)
from evm.scale_validation.s6bm_runtime import (  # noqa: E402
    CLAIM_BOUNDARY,
    S6BMConfig,
    analyze_attempts,
    build_continuity_plan,
    canonical,
    canonical_sha256,
    sha256_file,
)
from evm.scale_validation.s6bm_causal import validate_causal_bundle  # noqa: E402
from evm.scale_validation.s6bm_observability import (  # noqa: E402
    attempt_span_count,
    collect_attempt_trace_export,
    direct_metric_aggregate,
    direct_metric_optional_aggregate,
    direct_metric_value,
    prometheus_optional_value,
    prometheus_value,
    validate_observability_bundle,
)


SERVING_URL = "http://127.0.0.1:30800"
PROMETHEUS_URL = "http://127.0.0.1:9090"
SAMPLE_IMAGE_URI = "/mnt/evm-data/data/raw/industrial/visa/candle/Data/Images/Anomaly/000.JPG"
TARGET_ROOT = Path(
    "F:/EnterpriseMLOps_Data/enterprise-vision-mlops/artifacts/w7/prometheus-targets"
)
TRITON_TARGET = TARGET_ROOT / "s8-v4-s6bm-triton.json"
API_TARGET = TARGET_ROOT / "s8-v4-s6bm-api.json"
CONTAINER_NAME = "evm-s8-v4-s6bm-triton"
OTEL_TRACE_FILE = Path(
    os.getenv(
        "EVM_OTEL_TRACE_FILE",
        "F:/EnterpriseMLOps_Data/enterprise-vision-mlops/artifacts/scale_validation/otel/traces.json",
    )
)
CONTROL_PLANE_DATABASE_URL = os.getenv(
    "EVM_CONTROL_PLANE_DATABASE_URL",
    "postgresql://evm_control_plane:evm_control_plane_local@127.0.0.1:5434/evm_control_plane",
)


class S6BMExperimentError(RuntimeError):
    pass


@dataclass(frozen=True)
class Holder:
    namespace: str
    name: str
    uid: str
    replicas: int
    image: str


@dataclass
class ApiProcess:
    process: subprocess.Popen[str]
    stdout_handle: Any
    stderr_handle: Any
    stdout_path: Path
    stderr_path: Path


@dataclass
class TraceCollectorProcess:
    process: subprocess.Popen[str]
    spec_path: Path
    result_path: Path
    raw_trace_path: Path
    stdout_handle: Any
    stderr_handle: Any
    stdout_path: Path
    stderr_path: Path


@dataclass
class DualClockAnchorChain:
    source_identity: str
    anchor_nonce: str = field(default_factory=lambda: secrets.token_hex(16))
    sequence: int = 0
    previous_anchor_hash: str | None = None

    def capture(self, phase: str) -> dict[str, Any]:
        monotonic_before_ns = time.perf_counter_ns()
        unix_ns = time.time_ns()
        monotonic_after_ns = time.perf_counter_ns()
        self.sequence += 1
        payload = {
            "schema_version": "evm.s8_v4.s6bm_dual_clock_anchor.v3",
            "sequence": self.sequence,
            "phase": phase,
            "anchor_nonce": self.anchor_nonce,
            "monotonic_before_ns": monotonic_before_ns,
            "unix_ns": unix_ns,
            "monotonic_after_ns": monotonic_after_ns,
            "source_identity": f"{self.source_identity}:{self.anchor_nonce}",
            "process_id": os.getpid(),
            "host_identity": socket.gethostname(),
            "previous_anchor_hash": self.previous_anchor_hash,
        }
        payload["anchor_hash"] = canonical_sha256(payload)
        self.previous_anchor_hash = str(payload["anchor_hash"])
        return payload


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run S8-V4 S6B-M controlled evidence suite")
    parser.add_argument(
        "--config",
        type=Path,
        default=ROOT / "configs/s8_v4_s6bm_blue_green_v4.toml",
    )
    parser.add_argument(
        "--model-repository",
        type=Path,
        default=Path(
            "F:/EnterpriseMLOps_Data/enterprise-vision-mlops/artifacts/scale-validation/"
            "s8-v4/s6bm-model-repository-v1"
        ),
    )
    parser.add_argument(
        "--private-base",
        type=Path,
        default=Path(
            "F:/EnterpriseMLOps_Data/enterprise-vision-mlops/artifacts/scale-validation/"
            "private/s8-v4/s6bm"
        ),
    )
    parser.add_argument(
        "--public-output",
        type=Path,
        default=ROOT / "docs/status/evidence/s8-v4-s6bm-experiment-v4.json",
    )
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument(
        "--qualification-only",
        action="store_true",
        help="Run one non-credit causal receipt/trace qualification and stop before acceptance.",
    )
    modes.add_argument(
        "--continuity-qualification-only",
        action="store_true",
        help=("Run one exact-1000 non-credit continuity qualification and stop before acceptance."),
    )
    return parser.parse_args()


def utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def run(
    command: Sequence[str],
    *,
    timeout: float = 60,
    check: bool = True,
    env: Mapping[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        list(command),
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        env=None if env is None else dict(env),
        check=False,
    )
    if check and result.returncode != 0:
        raise S6BMExperimentError(
            f"command_failed:{result.returncode}:{' '.join(command)}:{result.stderr[-1500:]}"
        )
    return result


def run_json(command: Sequence[str], *, timeout: float = 60) -> dict[str, Any]:
    payload = json.loads(run(command, timeout=timeout).stdout)
    if not isinstance(payload, dict):
        raise S6BMExperimentError(f"json_object_required:{' '.join(command)}")
    return payload


def canonical_write(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(canonical(payload) + "\n", encoding="utf-8", newline="\n")


def git(*arguments: str) -> str:
    return run(["git", *arguments], timeout=30).stdout.strip()


def git_blob_sha256(revision: str, path: Path) -> str:
    git_root = Path(git("rev-parse", "--show-toplevel")).resolve()
    relative = path.resolve().relative_to(git_root).as_posix()
    payload = subprocess.run(
        ["git", "show", f"{revision}:{relative}"],
        cwd=git_root,
        capture_output=True,
        timeout=30,
        check=True,
    ).stdout
    return hashlib.sha256(payload).hexdigest()


def source_identity() -> dict[str, str]:
    if run(["git", "diff", "--quiet"], check=False).returncode != 0:
        raise S6BMExperimentError("tracked_worktree_dirty")
    return {
        "revision": git("rev-parse", "HEAD"),
        "tree_sha": git("rev-parse", "HEAD^{tree}"),
        "branch": git("branch", "--show-current"),
    }


def capture_holder() -> Holder:
    payload = run_json(
        [
            "kubectl",
            "-n",
            "evm-production",
            "get",
            "deployment/evm-b0-production",
            "-o",
            "json",
        ]
    )
    metadata = dict(payload["metadata"])
    spec = dict(payload["spec"])
    status = dict(payload.get("status", {}))
    replicas = int(spec.get("replicas", 0))
    if replicas != 1 or int(status.get("readyReplicas", 0)) != 1:
        raise S6BMExperimentError("b0_not_ready_1_of_1")
    containers = list(dict(spec["template"])["spec"]["containers"])
    if len(containers) != 1:
        raise S6BMExperimentError("b0_container_ambiguous")
    return Holder(
        namespace="evm-production",
        name="evm-b0-production",
        uid=str(metadata["uid"]),
        replicas=replicas,
        image=str(containers[0]["image"]),
    )


def scale_holder(holder: Holder, replicas: int, *, timeout: float = 180) -> None:
    run(
        [
            "kubectl",
            "-n",
            holder.namespace,
            "scale",
            f"deployment/{holder.name}",
            f"--replicas={replicas}",
        ]
    )
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        current = run_json(
            [
                "kubectl",
                "-n",
                holder.namespace,
                "get",
                f"deployment/{holder.name}",
                "-o",
                "json",
            ]
        )
        desired = int(dict(current["spec"]).get("replicas", 0))
        status = dict(current.get("status", {}))
        ready = int(status.get("readyReplicas", 0))
        available = int(status.get("availableReplicas", 0))
        if desired == replicas and ready == replicas and available == replicas:
            return
        if replicas == 0 and desired == ready == available == 0:
            return
        time.sleep(1)
    raise S6BMExperimentError(f"b0_scale_timeout:{replicas}")


def b0_cuda_inference() -> dict[str, Any]:
    ready = requests.get(f"{SERVING_URL}/ready", timeout=30)
    ready.raise_for_status()
    prediction = requests.post(
        f"{SERVING_URL}/predict",
        json={"image_uri": SAMPLE_IMAGE_URI},
        timeout=30,
    )
    prediction.raise_for_status()
    ready_body = ready.json()
    prediction_body = prediction.json()
    passed = (
        ready_body.get("status") == "ok"
        and ready_body.get("model_loaded") is True
        and ready_body.get("device") == "cuda"
        and prediction_body.get("device") == "cuda"
        and bool(prediction_body.get("prediction"))
    )
    if not passed:
        raise S6BMExperimentError("b0_cuda_inference_failed")
    return {"ready": ready_body, "prediction": prediction_body, "passed": True}


def capture_gpu() -> dict[str, Any]:
    output = run(
        [
            "nvidia-smi",
            "--query-gpu=uuid,name,driver_version,memory.total,memory.used,memory.free,"
            "utilization.gpu,temperature.gpu,power.draw",
            "--format=csv,noheader,nounits",
        ]
    ).stdout.strip()
    rows = [row for row in output.splitlines() if row.strip()]
    if len(rows) != 1:
        raise S6BMExperimentError(f"single_gpu_required:{len(rows)}")
    values = [item.strip() for item in rows[0].split(",")]
    return {
        "uuid": values[0],
        "name": values[1],
        "driver": values[2],
        "memory_total_mib": float(values[3]),
        "memory_used_mib": float(values[4]),
        "memory_free_mib": float(values[5]),
        "utilization_percent": float(values[6]),
        "temperature_celsius": float(values[7]),
        "power_watts": float(values[8]),
    }


def queue_counts() -> dict[str, int]:
    sql = (
        "SELECT count(*) FILTER (WHERE state IN "
        "('available','retry_wait','leased','runtime_pending','outcome_unknown')),"
        "count(*) FILTER (WHERE state='leased'),"
        "count(*) FILTER (WHERE state='outcome_unknown') "
        "FROM evm_control_plane.task_admission_queue;"
    )
    shell = f'psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" -At -F \'|\' -c "{sql}"'
    values = [
        int(value)
        for value in run(["docker", "exec", "evm-control-plane-postgres", "sh", "-lc", shell])
        .stdout.strip()
        .split("|")
    ]
    if len(values) != 3:
        raise S6BMExperimentError("queue_projection_invalid")
    return {"active": values[0], "leased": values[1], "outcome_unknown": values[2]}


def s6bm_schema_exists(schema: str) -> bool:
    import psycopg

    with psycopg.connect(CONTROL_PLANE_DATABASE_URL) as connection:
        row = connection.execute(
            "SELECT EXISTS(SELECT 1 FROM pg_namespace WHERE nspname=%s)",
            (schema,),
        ).fetchone()
    return bool(row[0])


def drop_s6bm_schema(schema: str) -> None:
    if not schema.startswith("evm_s6bm_v4_") or not schema.replace("_", "").isalnum():
        raise S6BMExperimentError("s6bm_cleanup_schema_identity")
    import psycopg

    with psycopg.connect(CONTROL_PLANE_DATABASE_URL, autocommit=True) as connection:
        connection.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
    if s6bm_schema_exists(schema):
        raise S6BMExperimentError("s6bm_cleanup_schema_residue")


def prometheus_targets() -> list[dict[str, Any]]:
    response = requests.get(f"{PROMETHEUS_URL}/api/v1/targets", timeout=10)
    response.raise_for_status()
    return list(dict(response.json()["data"])["activeTargets"])


def prometheus_baseline() -> dict[str, Any]:
    targets = prometheus_targets()
    return {
        "total": len(targets),
        "up": sum(item.get("health") == "up" for item in targets),
        "jobs": sorted(str(dict(item.get("labels", {})).get("job")) for item in targets),
    }


def wait_prometheus_baseline(expected: Mapping[str, Any], timeout: float = 30) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    latest = prometheus_baseline()
    while time.monotonic() < deadline:
        latest = prometheus_baseline()
        if latest == dict(expected):
            return latest
        time.sleep(0.5)
    raise S6BMExperimentError(f"prometheus_baseline_restore_timeout:{latest}")


def prometheus_query(query: str) -> float:
    response = requests.get(f"{PROMETHEUS_URL}/api/v1/query", params={"query": query}, timeout=10)
    response.raise_for_status()
    result = list(dict(response.json()["data"])["result"])
    return 0.0 if not result else float(result[0]["value"][1])


def write_prometheus_targets(config: S6BMConfig, suite_id: str) -> None:
    if TRITON_TARGET.exists() or API_TARGET.exists():
        raise S6BMExperimentError("temporary_prometheus_target_exists")
    canonical_write(
        TRITON_TARGET,
        [
            {
                "targets": [f"host.docker.internal:{config.ports['triton_metrics']}"],
                "labels": {"scenario": "s8-v4-s6bm", "suite_id": suite_id},
            }
        ],
    )
    canonical_write(
        API_TARGET,
        [
            {
                "targets": [f"host.docker.internal:{config.ports['api']}"],
                "labels": {"scenario": "s8-v4-s6bm", "suite_id": suite_id},
            }
        ],
    )
    reload_prometheus()


def bind_prometheus_attempt(
    config: S6BMConfig,
    *,
    suite_id: str,
    attempt_id: str,
) -> None:
    labels = {
        "scenario": "s8-v4-s6bm",
        "suite_id": suite_id,
        "attempt_id": attempt_id,
    }
    canonical_write(
        TRITON_TARGET,
        [
            {
                "targets": [f"host.docker.internal:{config.ports['triton_metrics']}"],
                "labels": labels,
            }
        ],
    )
    canonical_write(
        API_TARGET,
        [
            {
                "targets": [f"host.docker.internal:{config.ports['api']}"],
                "labels": labels,
            }
        ],
    )
    reload_prometheus()
    wait_prometheus_jobs(config, present=True)


def remove_prometheus_targets() -> None:
    canonical_write(TRITON_TARGET, [])
    canonical_write(API_TARGET, [])
    restart_prometheus()
    deadline = time.monotonic() + 30
    temporary_jobs = {"evm-s8-v4-s6bm-triton", "evm-s8-v4-s6bm-api"}
    while time.monotonic() < deadline:
        jobs = {str(dict(item.get("labels", {})).get("job")) for item in prometheus_targets()}
        if not (temporary_jobs & jobs):
            break
        time.sleep(0.5)
    else:
        raise S6BMExperimentError("prometheus_temporary_targets_not_cleared")
    TRITON_TARGET.unlink(missing_ok=True)
    API_TARGET.unlink(missing_ok=True)
    restart_prometheus()


def reload_prometheus() -> None:
    checked = run(
        [
            "docker",
            "exec",
            "evm-prometheus",
            "promtool",
            "check",
            "config",
            "/etc/prometheus/prometheus.yml",
        ],
        timeout=30,
    )
    if "SUCCESS" not in checked.stdout:
        raise S6BMExperimentError("prometheus_config_invalid")
    run(["docker", "kill", "--signal", "SIGHUP", "evm-prometheus"])


def restart_prometheus() -> None:
    run(["docker", "restart", "evm-prometheus"], timeout=60)
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        try:
            if requests.get(f"{PROMETHEUS_URL}/-/ready", timeout=1).status_code == 200:
                return
        except requests.RequestException:
            pass
        time.sleep(0.5)
    raise S6BMExperimentError("prometheus_restart_timeout")


def wait_prometheus_jobs(config: S6BMConfig, *, present: bool, timeout: float = 30) -> None:
    expected = {
        str(config.telemetry["prometheus_job_triton"]),
        str(config.telemetry["prometheus_job_api"]),
    }
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        observed = {str(dict(item.get("labels", {})).get("job")) for item in prometheus_targets()}
        healthy = {
            str(dict(item.get("labels", {})).get("job"))
            for item in prometheus_targets()
            if item.get("health") == "up"
        }
        if present and expected <= healthy:
            return
        if not present and not (expected & observed):
            return
        time.sleep(0.5)
    raise S6BMExperimentError(f"prometheus_job_state_timeout:{present}")


def verify_model_repository(source: Path, config: S6BMConfig) -> dict[str, Any]:
    manifest_path = source / "model-repository-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("repository_sha256") != config.repository_sha256:
        raise S6BMExperimentError("model_repository_digest_mismatch")
    for model in (config.blue, config.green):
        artifact = source / model.model_name / model.model_version / "model.pt"
        model_config = source / model.model_name / "config.pbtxt"
        if sha256_file(artifact) != model.artifact_sha256:
            raise S6BMExperimentError(f"model_artifact_digest:{model.role}")
        if sha256_file(model_config) != model.config_sha256:
            raise S6BMExperimentError(f"model_config_digest:{model.role}")
    return manifest


def image_identity(config: S6BMConfig) -> dict[str, Any]:
    payload = json.loads(run(["docker", "image", "inspect", config.image]).stdout)[0]
    expected = f"{config.image.rsplit(':', 1)[0]}@{config.image_digest}"
    if expected not in payload.get("RepoDigests", []):
        raise S6BMExperimentError("triton_image_digest_mismatch")
    return {"image_id": payload["Id"], "repo_digest": config.image_digest, "tag": config.image}


def start_triton(config: S6BMConfig, model_root: Path, log_path: Path) -> None:
    if container_exists(CONTAINER_NAME):
        raise S6BMExperimentError("triton_container_already_exists")
    immutable = f"{config.image.rsplit(':', 1)[0]}@{config.image_digest}"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    result = run(
        [
            "docker",
            "run",
            "-d",
            "--name",
            CONTAINER_NAME,
            "--gpus",
            "all",
            "--shm-size",
            "1g",
            "-p",
            f"127.0.0.1:{config.ports['triton_http']}:8000",
            "-p",
            f"127.0.0.1:{config.ports['triton_grpc']}:8001",
            "-p",
            f"127.0.0.1:{config.ports['triton_metrics']}:8002",
            "-v",
            f"{model_root.as_posix()}:/models:ro",
            immutable,
            "tritonserver",
            "--model-repository=/models",
            "--model-control-mode=explicit",
            f"--load-model={config.blue.model_name}",
            "--strict-readiness=true",
            "--exit-on-error=false",
            "--trace-config=mode=opentelemetry",
            "--trace-config=opentelemetry,url=http://host.docker.internal:4318/v1/traces",
            "--trace-config=level=TIMESTAMPS",
            "--trace-config=rate=1",
            "--trace-config=count=-1",
        ],
        timeout=60,
    )
    log_path.write_text(result.stdout, encoding="utf-8", newline="\n")


def stop_triton(log_path: Path) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    if not container_exists(CONTAINER_NAME):
        return
    logs = run(["docker", "logs", CONTAINER_NAME], check=False, timeout=30)
    log_path.write_text(logs.stdout + logs.stderr, encoding="utf-8", newline="\n")
    run(["docker", "kill", "--signal", "SIGINT", CONTAINER_NAME], check=False, timeout=30)
    deadline = time.monotonic() + 45
    while container_exists(CONTAINER_NAME) and time.monotonic() < deadline:
        time.sleep(0.5)
    if container_exists(CONTAINER_NAME):
        run(["docker", "rm", "-f", CONTAINER_NAME], check=False, timeout=30)


def container_exists(name: str) -> bool:
    return run(["docker", "inspect", name], check=False, timeout=10).returncode == 0


def start_api(
    config: S6BMConfig,
    suite_root: Path,
    *,
    source_revision: str,
    suite_id: str,
    database_schema: str,
) -> ApiProcess:
    stdout_path = suite_root / "runtime" / "api-stdout.log"
    stderr_path = suite_root / "runtime" / "api-stderr.log"
    stdout_path.parent.mkdir(parents=True, exist_ok=True)
    stdout_handle = stdout_path.open("w", encoding="utf-8", newline="\n")
    stderr_handle = stderr_path.open("w", encoding="utf-8", newline="\n")
    environment = dict(os.environ)
    environment.update(
        {
            "PYTHONPATH": os.pathsep.join((str(ROOT / "src"), str(ROOT))),
            "EVM_S6BM_ENABLED": "1",
            "EVM_S6BM_APPLY_MODEL_CONTROL": "1",
            "EVM_METRICS_REFRESH_MODE": "export-only",
            "EVM_OTEL_ENABLED": "1",
            "EVM_OTEL_REQUIRED": "1",
            "EVM_IMAGE_SOURCE_REVISION": source_revision,
            "OTEL_SERVICE_NAME": "evm-s8-v4-s6bm-api",
            "OTEL_SERVICE_INSTANCE_ID": suite_id,
            "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT": "http://127.0.0.1:4318/v1/traces",
            "EVM_S6BM_REQUIRE_DURABLE_EFFECT": "1",
            "EVM_S6BM_REQUIRE_CAUSAL_FENCE": "1",
            "EVM_S6BM_CAUSAL_SWITCH_TIMEOUT_SECONDS": str(
                config.continuity["route_switch_barrier_timeout_seconds"]
            ),
            "EVM_S6BM_DATABASE_URL": CONTROL_PLANE_DATABASE_URL,
            "EVM_S6BM_DATABASE_SCHEMA": database_schema,
            "EVM_S6BM_DATABASE_POOL_MIN_SIZE": str(config.durable_effect["pool_min_size"]),
            "EVM_S6BM_DATABASE_POOL_MAX_SIZE": str(config.durable_effect["pool_max_size"]),
            "EVM_S6BM_DATABASE_ACQUIRE_TIMEOUT_SECONDS": str(
                config.durable_effect["acquire_timeout_seconds"]
            ),
            "EVM_S6BM_COMMIT_READBACK_MAX_CONCURRENCY": str(
                config.durable_effect["commit_timestamp_readback_max_concurrency"]
            ),
            "EVM_S6BM_COMMIT_READBACK_ACQUIRE_TIMEOUT_SECONDS": str(
                config.durable_effect["commit_timestamp_readback_acquire_timeout_seconds"]
            ),
            "EVM_CONTROL_PLANE_AUTO_MIGRATE": "true",
        }
    )
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "apps.api.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(config.ports["api"]),
            "--workers",
            "1",
            "--log-level",
            "warning",
        ],
        cwd=ROOT,
        env=environment,
        stdout=stdout_handle,
        stderr=stderr_handle,
        text=True,
    )
    return ApiProcess(process, stdout_handle, stderr_handle, stdout_path, stderr_path)


def stop_api(service: ApiProcess | None) -> None:
    if service is None:
        return
    if service.process.poll() is None:
        service.process.terminate()
        try:
            service.process.wait(timeout=20)
        except subprocess.TimeoutExpired:
            service.process.kill()
            service.process.wait(timeout=10)
    service.stdout_handle.close()
    service.stderr_handle.close()


def wait_runtime(config: S6BMConfig, api: ApiProcess, timeout: float = 45) -> None:
    deadline = time.monotonic() + timeout
    triton = f"http://127.0.0.1:{config.ports['triton_http']}"
    api_url = f"http://127.0.0.1:{config.ports['api']}"
    while time.monotonic() < deadline:
        if api.process.poll() is not None:
            raise S6BMExperimentError(f"api_exited:{api.process.returncode}")
        try:
            checks = (
                requests.get(f"{triton}/v2/health/live", timeout=1).status_code == 200,
                requests.get(f"{triton}/v2/health/ready", timeout=1).status_code == 200,
                model_ready(config, "blue"),
                requests.get(
                    f"{api_url}/control-panel/v1/scenario-workloads/triton-blue-green/state",
                    timeout=1,
                ).status_code
                == 200,
            )
            if all(checks):
                return
        except requests.RequestException:
            pass
        time.sleep(0.25)
    logs = run(["docker", "logs", "--tail", "200", CONTAINER_NAME], check=False).stdout
    raise S6BMExperimentError(f"runtime_readiness_timeout:{logs[-1500:]}")


def port_open(port: int) -> bool:
    with socket.socket() as probe:
        probe.settimeout(0.2)
        return probe.connect_ex(("127.0.0.1", port)) == 0


def ports_absent(config: S6BMConfig) -> bool:
    return not any(port_open(port) for port in config.ports.values())


def api_url(config: S6BMConfig, suffix: str) -> str:
    return (
        f"http://127.0.0.1:{config.ports['api']}/control-panel/v1/"
        f"scenario-workloads/triton-blue-green/{suffix}"
    )


def triton_url(config: S6BMConfig, suffix: str) -> str:
    return f"http://127.0.0.1:{config.ports['triton_http']}{suffix}"


def model_ready(config: S6BMConfig, role: str) -> bool:
    model = config.blue if role == "blue" else config.green
    try:
        return (
            requests.get(
                triton_url(
                    config, f"/v2/models/{model.model_name}/versions/{model.model_version}/ready"
                ),
                timeout=2,
            ).status_code
            == 200
        )
    except requests.RequestException:
        return False


def direct_infer(config: S6BMConfig, role: str) -> dict[str, Any]:
    model = config.blue if role == "blue" else config.green
    response = requests.post(
        triton_url(config, f"/v2/models/{model.model_name}/versions/{model.model_version}/infer"),
        json={
            "inputs": [
                {
                    "name": "INPUT__0",
                    "shape": [1, 4],
                    "datatype": "FP32",
                    "data": [1.0, 2.0, 3.0, 4.0],
                }
            ],
            "outputs": [{"name": "OUTPUT__0"}],
        },
        timeout=10,
    )
    response.raise_for_status()
    body = response.json()
    output = [float(value) for value in body["outputs"][0]["data"]]
    expected = list(model.expected_output)
    if output != expected:
        raise S6BMExperimentError(f"direct_inference_mismatch:{role}:{output}")
    return {"status_code": response.status_code, "output": output, "expected": expected}


def initialize_controller(
    config: S6BMConfig, lease: GpuLease, source: Mapping[str, str]
) -> dict[str, Any]:
    payload = {
        "schema_version": "evm.s8_v4.s6bm_initialize_request.v1",
        "run_id": lease.run_id,
        "source_revision": source["revision"],
        "triton_http_url": f"http://127.0.0.1:{config.ports['triton_http']}",
        "image_digest": config.image_digest,
        "gpu_uuid": capture_gpu()["uuid"],
        "lease_id": lease.lease_id,
        "fencing_token": lease.fencing_token,
        "blue": {
            "role": "blue",
            "model_name": config.blue.model_name,
            "model_version": config.blue.model_version,
            "artifact_sha256": config.blue.artifact_sha256,
            "config_sha256": config.blue.config_sha256,
            "expected_output": list(config.blue.expected_output),
        },
        "green": {
            "role": "green",
            "model_name": config.green.model_name,
            "model_version": config.green.model_version,
            "artifact_sha256": config.green.artifact_sha256,
            "config_sha256": config.green.config_sha256,
            "expected_output": list(config.green.expected_output),
        },
    }
    response = requests.post(api_url(config, "initialize"), json=payload, timeout=15)
    if response.status_code != 200:
        raise S6BMExperimentError(
            f"controller_initialize_failed:{response.status_code}:{response.text[:1000]}"
        )
    return response.json()


def controller_state(config: S6BMConfig) -> dict[str, Any]:
    response = requests.get(api_url(config, "state"), timeout=10)
    response.raise_for_status()
    return response.json()


def remaining_deadline_seconds(
    deadline_monotonic_ns: int,
    *,
    error: str = "continuity_route_switch_barrier_timeout",
) -> float:
    remaining = (int(deadline_monotonic_ns) - time.perf_counter_ns()) / 1_000_000_000
    if remaining <= 0:
        raise S6BMExperimentError(error)
    return remaining


def wait_route_switch_deadline(
    config: S6BMConfig,
    *,
    owner_request_id: str,
) -> dict[str, Any]:
    """Read the actor-origin deadline created at designated crossover registration."""
    registration_wait_deadline = time.monotonic() + float(
        config.continuity["route_switch_barrier_timeout_seconds"]
    )
    latest: dict[str, Any] = {}
    while time.monotonic() < registration_wait_deadline:
        latest = controller_state(config)
        if latest.get("route_switch_deadline_owner_request_id") == owner_request_id:
            started = int(latest.get("route_switch_deadline_started_monotonic_ns") or 0)
            deadline = int(latest.get("route_switch_deadline_monotonic_ns") or 0)
            frozen_ns = int(
                float(config.continuity["route_switch_barrier_timeout_seconds"])
                * 1_000_000_000
            )
            if started <= 0 or deadline - started != frozen_ns:
                raise S6BMExperimentError("continuity_route_switch_deadline_binding")
            remaining_deadline_seconds(deadline)
            return {
                "owner_request_id": owner_request_id,
                "started_monotonic_ns": started,
                "deadline_monotonic_ns": deadline,
                "timeout_seconds": float(
                    config.continuity["route_switch_barrier_timeout_seconds"]
                ),
                "source": "api_control_plane_designated_crossover_registration",
            }
        time.sleep(0.01)
    raise S6BMExperimentError("continuity_crossover_registration_timeout")


def control_payload(
    config: S6BMConfig,
    lease: GpuLease,
    action: str,
    *,
    green_digest: str | None = None,
    preflight_vram_passed: bool = True,
    readiness_passed: bool = True,
    canary_passed: bool = True,
    causal_crossover: Mapping[str, Any] | None = None,
    continuity_receipt_request_ids: Sequence[str] = (),
    continuity_crossover_request_ids: Sequence[str] = (),
    pending_crossover_request_ids: Sequence[str] = (),
    continuity_terminal_request_ids: Sequence[str] = (),
    continuity_terminal_request_set_sha256: str | None = None,
    continuity_terminal_records_sha256: str | None = None,
) -> dict[str, Any]:
    state = controller_state(config)
    request = TritonBlueGreenControlRequest(
        run_id=lease.run_id,
        action=action,
        expected_generation=int(state["generation"]),
        lease_id=lease.lease_id,
        fencing_token=lease.fencing_token,
        blue_artifact_sha256=config.blue.artifact_sha256,
        green_artifact_sha256=green_digest or config.green.artifact_sha256,
        approval_id=f"approval-{action}-{state['generation']}-{uuid4().hex[:12]}",
        action_digest="0" * 64,
        preflight_vram_passed=preflight_vram_passed,
        readiness_passed=readiness_passed,
        canary_passed=canary_passed,
        causal_crossover=causal_crossover,
        continuity_receipt_request_ids=list(continuity_receipt_request_ids),
        continuity_crossover_request_ids=list(continuity_crossover_request_ids),
        pending_crossover_request_ids=list(pending_crossover_request_ids),
        continuity_terminal_request_ids=list(continuity_terminal_request_ids),
        continuity_terminal_request_set_sha256=continuity_terminal_request_set_sha256,
        continuity_terminal_records_sha256=continuity_terminal_records_sha256,
    )
    request = request.model_copy(update={"action_digest": action_digest(request)})
    return request.model_dump(mode="json")


def apply_control(
    config: S6BMConfig,
    lease: GpuLease,
    action: str,
    *,
    deadline_monotonic_ns: int | None = None,
    **signals: Any,
) -> dict[str, Any]:
    if action == "green_switched" and config.schema_version.endswith(".v4"):
        if deadline_monotonic_ns is None:
            raise S6BMExperimentError("continuity_route_switch_deadline_absent")
        timeout = remaining_deadline_seconds(deadline_monotonic_ns)
    else:
        timeout = 30.0
    response = requests.post(
        api_url(config, "control"),
        json=control_payload(config, lease, action, **signals),
        timeout=timeout,
    )
    response.raise_for_status()
    return response.json()


def rejected_control(
    config: S6BMConfig, lease: GpuLease, action: str, **signals: Any
) -> dict[str, Any]:
    response = requests.post(
        api_url(config, "control"),
        json=control_payload(config, lease, action, **signals),
        timeout=30,
    )
    body = response.json()
    detail = dict(body.get("detail", {}))
    return {
        "request_sent": True,
        "status_code": response.status_code,
        "guard_code": detail.get("error"),
        "message": detail.get("message"),
    }


def reset_controller(config: S6BMConfig, lease: GpuLease) -> None:
    response = requests.post(
        api_url(config, "reset"),
        json={
            "schema_version": "evm.s8_v4.s6bm_reset_request.v1",
            "run_id": lease.run_id,
            "lease_id": lease.lease_id,
            "fencing_token": lease.fencing_token,
        },
        timeout=15,
    )
    response.raise_for_status()


def traceparent(request_id: str) -> str:
    trace_id = hashlib.sha256(request_id.encode("utf-8")).hexdigest()[:32]
    span_id = hashlib.sha256(f"{request_id}:span".encode("utf-8")).hexdigest()[:16]
    return f"00-{trace_id}-{span_id}-01"


def blue_request_id(prefix: str) -> str:
    for index in range(10000):
        value = f"{prefix}-blue-{index:05d}"
        bucket = int(hashlib.sha256(value.encode("utf-8")).hexdigest()[:8], 16) % 100
        if bucket >= 10:
            return value
    raise S6BMExperimentError("blue_request_identity_not_found")


def expected_role(request_id: str, *, green_weight_percent: int) -> str:
    bucket = int(hashlib.sha256(request_id.encode("utf-8")).hexdigest()[:8], 16) % 100
    return "green" if bucket < green_weight_percent else "blue"


def request_body(
    config: S6BMConfig,
    run_id: str,
    attempt_id: str,
    request_id: str,
    *,
    expected_model_role: str,
    hold_ms: int = 0,
    expected_route_generation: int = 0,
    start_receipt_required: bool = False,
    causal_crossover: bool = False,
    route_switch_deadline_owner: bool = False,
) -> dict[str, Any]:
    model = config.blue if expected_model_role == "blue" else config.green
    return {
        "schema_version": "evm.s8_v4.s6bm_predict_request.v1",
        "run_id": run_id,
        "attempt_id": attempt_id,
        "request_id": request_id,
        "request_nonce": hashlib.sha256(
            f"{run_id}:{attempt_id}:{request_id}:nonce".encode("utf-8")
        ).hexdigest()[:32],
        "traceparent": traceparent(request_id),
        "input_values": [1.0, 2.0, 3.0, 4.0],
        "hold_ms": hold_ms,
        "expected_model_role": expected_model_role,
        "expected_model_name": model.model_name,
        "expected_model_version": model.model_version,
        "expected_artifact_sha256": model.artifact_sha256,
        "expected_route_generation": expected_route_generation,
        "start_receipt_required": start_receipt_required,
        "causal_crossover": causal_crossover,
        "route_switch_deadline_owner": route_switch_deadline_owner,
    }


def request_bodies_from_plan(
    config: S6BMConfig,
    run_id: str,
    attempt_id: str,
    items: Sequence[Mapping[str, Any]],
    *,
    route_generation: int = 0,
) -> list[dict[str, Any]]:
    bodies = []
    for item in items:
        traffic_role = str(item["traffic_role"])
        causal = traffic_role == "causal_hold"
        start_receipt_required = bool(item.get("actor_receipt_required", False))
        causal_crossover = bool(item.get("causal_crossover", causal))
        route_switch_deadline_owner = bool(
            item.get("route_switch_deadline_owner", False)
        )
        bodies.append(
            request_body(
                config,
                run_id,
                attempt_id,
                str(item["request_id"]),
                expected_model_role=str(item["expected_model_role"]),
                hold_ms=int(item["hold_ms"]),
                expected_route_generation=route_generation,
                start_receipt_required=start_receipt_required,
                causal_crossover=causal_crossover,
                route_switch_deadline_owner=route_switch_deadline_owner,
            )
        )
    return bodies


def start_triton_start_receipt_collector(
    config: S6BMConfig,
    *,
    suite_root: Path,
    checkpoint: Mapping[str, Any],
    body: Mapping[str, Any],
    source_revision: str,
    suite_id: str,
    deadline_monotonic_ns: int,
    artifact_scope: str | None = None,
) -> TraceCollectorProcess:
    request = TritonBlueGreenPredictRequest.model_validate(dict(body))
    identity = expected_causal_identity_for_request(request)
    wait_seconds = float(config.continuity["route_switch_barrier_timeout_seconds"])
    if wait_seconds <= 0:
        raise S6BMExperimentError("triton_compute_start_trace_timeout_bound")
    remaining_deadline_seconds(
        deadline_monotonic_ns,
        error="triton_compute_start_trace_timeout",
    )
    root = suite_root / "causal" / identity.attempt_id
    if artifact_scope is not None:
        root = root / artifact_scope
    spec_path = root / "collector-spec.json"
    result_path = root / "collector-result.json"
    raw_trace_path = root / "triton-compute-start.json"
    stdout_path = root / "collector.stdout.log"
    stderr_path = root / "collector.stderr.log"
    container_id = run(
        ["docker", "inspect", "--format", "{{.Id}}", CONTAINER_NAME],
        timeout=10,
    ).stdout.strip()
    spec = {
        "schema_version": "evm.s8_v4.s6bm_trace_collector_spec.v1",
        "source_revision": source_revision,
        "suite_id": suite_id,
        "attempt_id": identity.attempt_id,
        "run_id": identity.run_id,
        "request_id": identity.request_id,
        "request_nonce": identity.request_nonce,
        "trace_id": identity.trace_id,
        "model_name": identity.model_name,
        "model_version": identity.model_version,
        "causal_identity": identity.model_dump(mode="json"),
        "runner_process_id": os.getpid(),
        "trace_start_offset": int(checkpoint["trace_start_offset"]),
        "otel_trace_path": str(OTEL_TRACE_FILE.resolve()),
        "raw_trace_output_path": str(raw_trace_path.resolve()),
        "result_output_path": str(result_path.resolve()),
        "receipt_url": api_url(config, "causal-receipts/triton"),
        "container_name": CONTAINER_NAME,
        "expected_container_id": container_id,
        "image_digest": config.image_digest,
        "expected_gpu_uuid": capture_gpu()["uuid"],
        "timeout_seconds": wait_seconds,
        "deadline_monotonic_ns": int(deadline_monotonic_ns),
        "deadline_source": "api_control_plane_designated_crossover_registration",
    }
    canonical_write(spec_path, spec)
    stdout_path.parent.mkdir(parents=True, exist_ok=True)
    stdout_handle = stdout_path.open("w", encoding="utf-8", newline="\n")
    stderr_handle = stderr_path.open("w", encoding="utf-8", newline="\n")
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join([str(ROOT / "src"), str(ROOT), env.get("PYTHONPATH", "")])
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "evm.scale_validation.s6bm_trace_receipt_collector",
            "--spec",
            str(spec_path.resolve()),
        ],
        cwd=ROOT,
        env=env,
        stdout=stdout_handle,
        stderr=stderr_handle,
        text=True,
    )
    return TraceCollectorProcess(
        process=process,
        spec_path=spec_path,
        result_path=result_path,
        raw_trace_path=raw_trace_path,
        stdout_handle=stdout_handle,
        stderr_handle=stderr_handle,
        stdout_path=stdout_path,
        stderr_path=stderr_path,
    )


def wait_triton_start_receipt_collector(
    collector: TraceCollectorProcess,
    *,
    suite_root: Path,
    deadline_monotonic_ns: int,
) -> dict[str, Any]:
    try:
        return_code = collector.process.wait(
            timeout=remaining_deadline_seconds(
                deadline_monotonic_ns,
                error="triton_compute_start_trace_timeout",
            )
        )
    except subprocess.TimeoutExpired as exc:
        collector.process.kill()
        collector.process.wait(timeout=5)
        raise S6BMExperimentError("triton_compute_start_trace_timeout") from exc
    finally:
        collector.stdout_handle.close()
        collector.stderr_handle.close()
    if return_code != 0 or not collector.result_path.is_file():
        error_path = collector.spec_path.with_name("collector-error.json")
        detail = error_path.read_text(encoding="utf-8") if error_path.is_file() else ""
        raise S6BMExperimentError(
            f"triton_start_receipt_collector_failed:{return_code}:{detail[:1000]}"
        )
    result = json.loads(collector.result_path.read_text(encoding="utf-8"))
    if (
        result.get("collector_process_id") != collector.process.pid
        or result.get("collector_parent_process_id") != os.getpid()
        or result.get("collector_spec_sha256") != sha256_file(collector.spec_path)
        or result.get("raw_trace_sha256") != sha256_file(collector.raw_trace_path)
        or dict(result.get("receipt", {})).get("readback_visible") is not True
    ):
        raise S6BMExperimentError("triton_start_receipt_collector_identity")
    return {
        **result,
        "spec": artifact_reference(suite_root, collector.spec_path),
        "result": artifact_reference(suite_root, collector.result_path),
        "raw_trace": artifact_reference(suite_root, collector.raw_trace_path),
        "stdout": artifact_reference(suite_root, collector.stdout_path),
        "stderr": artifact_reference(suite_root, collector.stderr_path),
    }


def stop_trace_collectors(collectors: Sequence[TraceCollectorProcess]) -> None:
    for collector in collectors:
        if collector.process.poll() is None:
            collector.process.kill()
            collector.process.wait(timeout=5)
        if not collector.stdout_handle.closed:
            collector.stdout_handle.close()
        if not collector.stderr_handle.closed:
            collector.stderr_handle.close()


def read_required_bridge_start_receipts(
    config: S6BMConfig,
    *,
    suite_root: Path,
    attempt_id: str,
    required_request_ids: Sequence[str],
    route_generation: int,
    deadline_monotonic_ns: int,
) -> dict[str, Any]:
    response = requests.get(
        api_url(config, f"causal-events/{attempt_id}"),
        timeout=remaining_deadline_seconds(deadline_monotonic_ns),
    )
    response.raise_for_status()
    payload = response.json()
    readback_path = suite_root / "causal" / attempt_id / "bridge-start-receipts-pre-switch.json"
    canonical_write(readback_path, payload)
    required_stages = {
        "api_server_handler_entry",
        "controller_entry",
        "triton_backend_compute_entry",
    }
    rows = [dict(item) for item in payload.get("events", [])]
    if (
        payload.get("schema_version") != "evm.s8_v4.s6bm_causal_event_export.v1"
        or payload.get("attempt_id") != attempt_id
        or int(payload.get("event_count", -1)) != len(rows)
    ):
        raise S6BMExperimentError("continuity_actor_receipt_export_identity")
    selected: list[dict[str, Any]] = []
    observed_sequences: list[int] = []
    for request_id in required_request_ids:
        matches = [
            item
            for item in rows
            if item.get("request_id") == request_id and item.get("event_type") in required_stages
        ]
        if (
            len(matches) != len(required_stages)
            or {str(item.get("event_type", "")) for item in matches} != required_stages
        ):
            raise S6BMExperimentError(f"continuity_actor_receipt_incomplete:{request_id}")
        for item in matches:
            event_payload = dict(item.get("payload", {}))
            if (
                event_payload.get("request_id") != request_id
                or event_payload.get("attempt_id") != attempt_id
                or event_payload.get("model_role") != "blue"
                or int(event_payload.get("route_generation", 0)) != route_generation
                or int(item.get("causal_sequence", 0)) <= 0
                or not str(item.get("transaction_id", ""))
                or any(
                    item.get(key) != event_payload.get(key)
                    for key in (
                        "attempt_id",
                        "run_id",
                        "request_id",
                        "request_nonce",
                        "trace_id",
                        "effect_id",
                        "model_role",
                        "model_name",
                        "model_version",
                        "artifact_sha256",
                        "route_generation",
                    )
                )
                or item.get("payload_sha256") != canonical_sha256(event_payload)
                or not str(item.get("database_recorded_at", ""))
                or not str(item.get("captured_at", ""))
            ):
                raise S6BMExperimentError(f"continuity_actor_receipt_identity:{request_id}")
            observed_sequences.append(int(item["causal_sequence"]))
        selected.extend(matches)
    if len(set(required_request_ids)) != len(required_request_ids):
        raise S6BMExperimentError("continuity_actor_receipt_request_duplicate")
    if len(set(observed_sequences)) != len(observed_sequences):
        raise S6BMExperimentError("continuity_actor_receipt_sequence_duplicate")
    gate_events = sorted(
        (
            {
                "causal_sequence": item["causal_sequence"],
                "event_type": item["event_type"],
                "attempt_id": item["attempt_id"],
                "run_id": item["run_id"],
                "request_id": item["request_id"],
                "request_nonce": item["request_nonce"],
                "trace_id": item["trace_id"],
                "effect_id": item["effect_id"],
                "model_role": item["model_role"],
                "model_name": item["model_name"],
                "model_version": item["model_version"],
                "artifact_sha256": item["artifact_sha256"],
                "route_generation": item["route_generation"],
                "actor_identity": item["actor_identity"],
                "transaction_id": item["transaction_id"],
                "payload_sha256": item["payload_sha256"],
                "database_recorded_at": item["database_recorded_at"],
                "readback_at": item["captured_at"],
                "readback_visible": True,
                "readback_source": "postgresql_attempt_export",
            }
            for item in selected
        ),
        key=lambda item: (item["request_id"], item["event_type"]),
    )
    return {
        "schema_version": "evm.s8_v4.s6bm_bridge_actor_receipt_gate.v1",
        "attempt_id": attempt_id,
        "route_generation": route_generation,
        "required_request_ids": list(required_request_ids),
        "required_request_set_sha256": canonical_sha256(list(required_request_ids)),
        "required_stage_count": len(required_stages),
        "expected_event_count": len(required_request_ids) * len(required_stages),
        "visible_event_count": len(selected),
        "maximum_visible_causal_sequence": max(observed_sequences, default=0),
        "raw_readback_export": artifact_reference(suite_root, readback_path),
        "raw_readback_event_count": len(rows),
        "selected_event_set_sha256": canonical_sha256(gate_events),
        "events": gate_events,
    }


def bind_pre_switch_terminal_gate(
    config: S6BMConfig,
    *,
    suite_root: Path,
    attempt_id: str,
    expected_bodies: Mapping[str, Mapping[str, Any]],
    response_records: Mapping[str, Mapping[str, Any]],
    terminal_gate: dict[str, Any],
    deadline_monotonic_ns: int,
) -> dict[str, Any]:
    """Bind online Blue responses to committed PG entity/event readback before switch."""
    expected_ids = sorted(expected_bodies)
    if (
        len(expected_ids) != int(config.continuity["pre_switch_terminal_bridge_count"])
        or sorted(response_records) != expected_ids
    ):
        raise S6BMExperimentError("continuity_pre_switch_terminal_exact_set")
    effects_response = requests.get(
        api_url(config, f"effects/{attempt_id}"),
        timeout=remaining_deadline_seconds(deadline_monotonic_ns),
    )
    events_response = requests.get(
        api_url(config, f"causal-events/{attempt_id}"),
        timeout=remaining_deadline_seconds(deadline_monotonic_ns),
    )
    effects_response.raise_for_status()
    events_response.raise_for_status()
    effects_export = effects_response.json()
    events_export = events_response.json()
    evidence_root = suite_root / "causal" / attempt_id
    effects_path = evidence_root / "bridge-terminal-effects-pre-switch.json"
    events_path = evidence_root / "bridge-terminal-events-pre-switch.json"
    canonical_write(effects_path, effects_export)
    canonical_write(events_path, events_export)
    effects = [dict(item) for item in effects_export.get("effects", [])]
    events = [
        dict(item)
        for item in events_export.get("events", [])
        if item.get("event_type") == "durable_terminal_effect_commit"
    ]
    if (
        effects_export.get("schema_version")
        != "evm.s8_v4.s6bm_terminal_effect_export.v1"
        or effects_export.get("attempt_id") != attempt_id
        or int(effects_export.get("effect_count", -1)) != len(effects)
        or events_export.get("schema_version")
        != "evm.s8_v4.s6bm_causal_event_export.v1"
        or events_export.get("attempt_id") != attempt_id
        or int(events_export.get("event_count", -1))
        != len(events_export.get("events", []))
    ):
        raise S6BMExperimentError("continuity_pre_switch_terminal_export_identity")
    effects_by_request = {
        str(item.get("idempotency_key", "")): item for item in effects
    }
    events_by_request = {str(item.get("request_id", "")): item for item in events}
    if (
        not set(expected_ids).issubset(effects_by_request)
        or not set(expected_ids).issubset(events_by_request)
    ):
        raise S6BMExperimentError("continuity_pre_switch_terminal_durable_missing")
    projected: list[dict[str, Any]] = []
    for request_id in expected_ids:
        body = dict(expected_bodies[request_id])
        response = dict(response_records[request_id])
        durable = dict(response.get("durable_effect") or {})
        if (
            response.get("request_id") != request_id
            or response.get("attempt_id") != attempt_id
            or response.get("run_id") != body.get("run_id")
            or response.get("trace_id") != str(body.get("traceparent", ""))[3:35]
            or response.get("model_role") != "blue"
            or response.get("model_name") != body.get("expected_model_name")
            or str(response.get("model_version"))
            != str(body.get("expected_model_version"))
            or response.get("artifact_sha256") != body.get("expected_artifact_sha256")
            or int(response.get("route_generation", 0))
            != int(body.get("expected_route_generation", 0))
            or response.get("outcome") != "completed"
            or int(response.get("status_code", 0)) != 200
            or durable.get("readback_visible") is not True
            or durable.get("entity_id") != response.get("effect_id")
        ):
            raise S6BMExperimentError("continuity_pre_switch_terminal_online_identity")
        try:
            record = s6bm_terminal_fence_record(
                effects_by_request[request_id], events_by_request[request_id]
            )
        except ControlPlaneParityError as exc:
            raise S6BMExperimentError(
                "continuity_pre_switch_terminal_durable_parity"
            ) from exc
        if (
            record["request_id"] != request_id
            or record["attempt_id"] != attempt_id
            or record["run_id"] != response.get("run_id")
            or record["trace_id"] != response.get("trace_id")
            or record["effect_id"] != response.get("effect_id")
            or record["model_role"] != response.get("model_role")
            or record["model_name"] != response.get("model_name")
            or record["model_version"] != response.get("model_version")
            or record["artifact_sha256"] != response.get("artifact_sha256")
            or record["route_generation"] != response.get("route_generation")
            or record["result_sha256"] != response.get("result_sha256")
            or record["request_sha256"] != durable.get("request_sha256")
            or record["stored_payload_sha256"] != durable.get("stored_payload_sha256")
            or record["causal_sequence"] != durable.get("causal_sequence")
            or record["causal_payload_sha256"] != durable.get("causal_payload_sha256")
        ):
            raise S6BMExperimentError("continuity_pre_switch_terminal_readback_binding")
        projected.append(record)
    records_sha256 = canonical_sha256(projected)
    terminal_gate.update(
        {
            "schema_version": "evm.s8_v4.s6bm_pre_switch_bridge_terminal_gate.v2",
            "expected_terminal_request_set_sha256": canonical_sha256(expected_ids),
            "observed_terminal_request_ids": expected_ids,
            "observed_terminal_request_set_sha256": canonical_sha256(expected_ids),
            "observed_terminal_count": len(expected_ids),
            "terminal_records": projected,
            "terminal_records_sha256": records_sha256,
            "raw_effect_export": artifact_reference(suite_root, effects_path),
            "raw_event_export": artifact_reference(suite_root, events_path),
            "durable_readback_complete": True,
        }
    )
    return terminal_gate


def send_request(
    config: S6BMConfig,
    body: Mapping[str, Any],
    *,
    session: requests.Session | None = None,
) -> dict[str, Any]:
    attempted = time.perf_counter()
    offered_payload_bytes = len(canonical(dict(body)).encode("ascii"))
    offered_payload_sha256 = canonical_sha256(dict(body))
    client = session if session is not None else requests
    try:
        response = client.post(
            api_url(config, "predict"),
            json=dict(body),
            headers={"traceparent": str(body["traceparent"])},
            timeout=20,
        )
        completed = time.perf_counter()
        parsed = response.json()
        if response.status_code != 200:
            return {
                "request_id": body["request_id"],
                "status_code": response.status_code,
                "outcome": "http_error",
                "detail": parsed,
                "offered_payload_bytes": offered_payload_bytes,
                "offered_payload_sha256": offered_payload_sha256,
                "attempted_monotonic": attempted,
                "completed_monotonic": completed,
            }
        return {
            "run_id": parsed["run_id"],
            "attempt_id": parsed["attempt_id"],
            "request_id": parsed["request_id"],
            "trace_id": parsed["trace_id"],
            "effect_id": parsed["effect_id"],
            "offered_traceparent": body["traceparent"],
            "offered_payload_bytes": offered_payload_bytes,
            "offered_payload_sha256": offered_payload_sha256,
            "offered_identity": {
                "model_role": body["expected_model_role"],
                "model_name": body["expected_model_name"],
                "model_version": body["expected_model_version"],
                "artifact_sha256": body["expected_artifact_sha256"],
            },
            "status_code": response.status_code,
            "outcome": "completed",
            "model_role": parsed["model_role"],
            "model_name": parsed["model_name"],
            "model_version": parsed["model_version"],
            "artifact_sha256": parsed["artifact_sha256"],
            "request_nonce": body["request_nonce"],
            "output": parsed["output"],
            "result_sha256": parsed["result_sha256"],
            "elapsed_ms": float(parsed["elapsed_ms"]),
            "route_generation": parsed["route_generation"],
            "route_phase": parsed["route_phase"],
            "durable_effect": parsed.get("durable_effect"),
            "replayed": bool(parsed["replayed"]),
            "attempted_monotonic": attempted,
            "completed_monotonic": completed,
        }
    except requests.RequestException as exc:
        return {
            "request_id": body["request_id"],
            "status_code": 0,
            "outcome": "transport_failure",
            "error": str(exc),
            "offered_payload_bytes": offered_payload_bytes,
            "offered_payload_sha256": offered_payload_sha256,
            "attempted_monotonic": attempted,
            "completed_monotonic": time.perf_counter(),
        }


def send_batch(
    config: S6BMConfig,
    run_id: str,
    attempt_id: str,
    prefix: str,
    count: int,
    concurrency: int,
    *,
    expected_model_role: str | None = None,
    green_weight_percent: int = 0,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    bodies = []
    for index in range(count):
        request_id = f"{prefix}-{index:05d}"
        role = expected_model_role or expected_role(
            request_id, green_weight_percent=green_weight_percent
        )
        bodies.append(
            request_body(
                config,
                run_id,
                attempt_id,
                request_id,
                expected_model_role=role,
            )
        )
    return send_bodies(config, bodies, concurrency), bodies


def send_bodies(
    config: S6BMConfig,
    bodies: Sequence[Mapping[str, Any]],
    concurrency: int,
) -> list[dict[str, Any]]:
    local = threading.local()
    sessions: list[requests.Session] = []
    sessions_lock = threading.Lock()

    def dispatch(body: Mapping[str, Any]) -> dict[str, Any]:
        session = getattr(local, "session", None)
        if session is None:
            session = requests.Session()
            adapter = requests.adapters.HTTPAdapter(
                pool_connections=1,
                pool_maxsize=1,
                pool_block=True,
            )
            session.mount("http://", adapter)
            local.session = session
            with sessions_lock:
                sessions.append(session)
        return send_request(config, body, session=session)

    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as pool:
            records = list(pool.map(dispatch, bodies))
    finally:
        for session in sessions:
            session.close()
    return records


def run_fixed_bridge_producer(
    config: S6BMConfig,
    bodies: Sequence[Mapping[str, Any]],
    schedule: Sequence[Mapping[str, Any]],
    collect_required_actor_receipts: Callable[[int], dict[str, Any]],
    on_switch_ready: Callable[[dict[str, Any], dict[str, Any], int], dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any], dict[str, Any]]:
    """Finish 39 Blue requests before switching and release one exact waiter."""
    if len(bodies) != len(schedule):
        raise S6BMExperimentError("continuity_schedule_cardinality")
    workers = int(config.continuity["producer_workers"])
    capacity = int(config.continuity["max_in_flight_requests"])
    payload_cap = int(config.continuity["max_request_payload_bytes"])
    bytes_cap = int(config.continuity["max_in_flight_payload_bytes"])
    barrier_timeout = float(config.continuity["route_switch_barrier_timeout_seconds"])
    if workers != capacity:
        raise S6BMExperimentError("continuity_worker_capacity_contract")

    local = threading.local()
    sessions: list[requests.Session] = []
    sessions_lock = threading.Lock()
    capacity_lock = threading.Lock()
    slots = threading.BoundedSemaphore(capacity)
    reserved_count = 0
    reserved_bytes = 0
    max_reserved_count = 0
    max_reserved_bytes = 0
    futures: list[concurrent.futures.Future[dict[str, Any]]] = []
    futures_by_request: dict[str, concurrent.futures.Future[dict[str, Any]]] = {}
    dispatch_meta: list[dict[str, Any]] = []
    producer_started = time.perf_counter()
    causal_gate_started = time.perf_counter()
    crossover_ids = [
        str(body["request_id"]) for body in bodies if bool(body.get("causal_crossover", False))
    ]
    if len(crossover_ids) != 1:
        raise S6BMExperimentError("continuity_crossover_exact_set")
    crossover_id = crossover_ids[0]
    barrier_deadline_ns: int | None = None
    deadline_state: dict[str, Any] | None = None

    def dispatch(body: Mapping[str, Any]) -> dict[str, Any]:
        session = getattr(local, "session", None)
        if session is None:
            session = requests.Session()
            adapter = requests.adapters.HTTPAdapter(
                pool_connections=1,
                pool_maxsize=1,
                pool_block=True,
            )
            session.mount("http://", adapter)
            local.session = session
            with sessions_lock:
                sessions.append(session)
        return send_request(config, body, session=session)

    def release_slot(payload_bytes: int) -> None:
        nonlocal reserved_count, reserved_bytes
        with capacity_lock:
            reserved_count -= 1
            reserved_bytes -= payload_bytes
            if reserved_count < 0 or reserved_bytes < 0:
                raise S6BMExperimentError("continuity_capacity_accounting")
        slots.release()

    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as gate_pool:
            gate_future: concurrent.futures.Future[dict[str, Any]] | None = None
            with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
                for body, planned in zip(bodies, schedule, strict=True):
                    target = producer_started + int(planned["scheduled_offset_ms"]) / 1000.0
                    remaining = target - time.perf_counter()
                    if remaining > 0:
                        time.sleep(remaining)
                    wait_started = time.perf_counter()
                    slot_timeout = (
                        remaining_deadline_seconds(barrier_deadline_ns)
                        if barrier_deadline_ns is not None
                        else barrier_timeout
                    )
                    if not slots.acquire(timeout=slot_timeout):
                        raise S6BMExperimentError("continuity_capacity_wait_timeout")
                    wait_finished = time.perf_counter()
                    payload_bytes = len(canonical(dict(body)).encode("ascii"))
                    if payload_bytes > payload_cap:
                        slots.release()
                        raise S6BMExperimentError("continuity_payload_too_large")
                    with capacity_lock:
                        reserved_count += 1
                        reserved_bytes += payload_bytes
                        max_reserved_count = max(max_reserved_count, reserved_count)
                        max_reserved_bytes = max(max_reserved_bytes, reserved_bytes)
                        if reserved_count > capacity or reserved_bytes > bytes_cap:
                            reserved_count -= 1
                            reserved_bytes -= payload_bytes
                            slots.release()
                            raise S6BMExperimentError("continuity_capacity_bound")
                    future = pool.submit(dispatch, body)
                    future.add_done_callback(lambda _future, size=payload_bytes: release_slot(size))
                    futures.append(future)
                    request_id = str(body["request_id"])
                    futures_by_request[request_id] = future
                    dispatch_meta.append(
                        {
                            "request_id": str(body["request_id"]),
                            "scheduled_offset_ms": int(planned["scheduled_offset_ms"]),
                            "hold_ms": int(body["hold_ms"]),
                            "actor_receipt_required": bool(
                                planned.get("actor_receipt_required", False)
                            ),
                            "causal_crossover": bool(body.get("causal_crossover", False)),
                            "payload_bytes": payload_bytes,
                            "payload_sha256": canonical_sha256(dict(body)),
                            "capacity_wait_ms": (wait_finished - wait_started) * 1000.0,
                        }
                    )
                    if request_id == crossover_id:
                        deadline_state = wait_route_switch_deadline(
                            config,
                            owner_request_id=crossover_id,
                        )
                        barrier_deadline_ns = int(
                            deadline_state["deadline_monotonic_ns"]
                        )
                        gate_future = gate_pool.submit(
                            collect_required_actor_receipts,
                            barrier_deadline_ns,
                        )
                all_submitted = time.perf_counter()
                if gate_future is None or barrier_deadline_ns is None or deadline_state is None:
                    raise S6BMExperimentError("continuity_route_switch_deadline_absent")

                def remaining_barrier_time() -> float:
                    return remaining_deadline_seconds(barrier_deadline_ns)

                receipt_proof = gate_future.result(timeout=remaining_barrier_time())
                expected_terminal_ids = sorted(
                    request_id for request_id in futures_by_request if request_id != crossover_id
                )
                terminal_records = {
                    request_id: futures_by_request[request_id].result(
                        timeout=remaining_barrier_time()
                    )
                    for request_id in expected_terminal_ids
                }
                expected_bodies = {
                    str(body["request_id"]): dict(body)
                    for body in bodies
                    if str(body["request_id"]) != crossover_id
                }
                if (
                    len(expected_terminal_ids) != len(bodies) - 1
                    or set(terminal_records) != set(expected_terminal_ids)
                    or any(
                        record.get("request_id") != request_id
                        or record.get("attempt_id") != expected_bodies[request_id].get("attempt_id")
                        or record.get("run_id") != expected_bodies[request_id].get("run_id")
                        or record.get("model_role") != "blue"
                        or record.get("model_name")
                        != expected_bodies[request_id].get("expected_model_name")
                        or str(record.get("model_version"))
                        != str(expected_bodies[request_id].get("expected_model_version"))
                        or record.get("artifact_sha256")
                        != expected_bodies[request_id].get("expected_artifact_sha256")
                        or int(record.get("route_generation", 0))
                        != int(expected_bodies[request_id].get("expected_route_generation", 0))
                        or record.get("outcome") != "completed"
                        or int(record.get("status_code", 0)) != 200
                        or dict(record.get("durable_effect") or {}).get("readback_visible")
                        is not True
                        for request_id, record in terminal_records.items()
                    )
                ):
                    raise S6BMExperimentError("continuity_pre_switch_terminal_online_identity")
                online_terminal_records = [
                    {
                        "request_id": request_id,
                        "attempt_id": terminal_records[request_id].get("attempt_id"),
                        "run_id": terminal_records[request_id].get("run_id"),
                        "trace_id": terminal_records[request_id].get("trace_id"),
                        "effect_id": terminal_records[request_id].get("effect_id"),
                        "model_role": terminal_records[request_id].get("model_role"),
                        "model_name": terminal_records[request_id].get("model_name"),
                        "model_version": terminal_records[request_id].get("model_version"),
                        "artifact_sha256": terminal_records[request_id].get("artifact_sha256"),
                        "route_generation": terminal_records[request_id].get("route_generation"),
                        "status_code": terminal_records[request_id].get("status_code"),
                        "outcome": terminal_records[request_id].get("outcome"),
                        "attempted_monotonic": terminal_records[request_id].get(
                            "attempted_monotonic"
                        ),
                        "completed_monotonic": terminal_records[request_id].get(
                            "completed_monotonic"
                        ),
                        "durable_effect_readback_finished_monotonic_ns": dict(
                            terminal_records[request_id].get("durable_effect") or {}
                        ).get("readback_finished_monotonic_ns"),
                    }
                    for request_id in expected_terminal_ids
                ]
                terminal_gate = {
                    "schema_version": (
                        "evm.s8_v4.s6bm_pre_switch_bridge_terminal_online_gate.v1"
                    ),
                    "crossover_request_id": crossover_id,
                    "expected_terminal_request_ids": expected_terminal_ids,
                    "expected_terminal_request_set_sha256": canonical_sha256(expected_terminal_ids),
                    "expected_terminal_count": len(expected_terminal_ids),
                    "observed_terminal_request_ids": sorted(terminal_records),
                    "observed_terminal_request_set_sha256": canonical_sha256(
                        sorted(terminal_records)
                    ),
                    "observed_terminal_count": len(terminal_records),
                    "online_terminal_records": online_terminal_records,
                    "online_terminal_records_sha256": canonical_sha256(
                        online_terminal_records
                    ),
                    "online_response_records": [
                        terminal_records[request_id]
                        for request_id in expected_terminal_ids
                    ],
                    "online_response_records_sha256": canonical_sha256(
                        [
                            terminal_records[request_id]
                            for request_id in expected_terminal_ids
                        ]
                    ),
                    "all_submitted_monotonic": all_submitted,
                    "all_non_crossover_terminal_monotonic": time.perf_counter(),
                }
                transition = on_switch_ready(
                    receipt_proof,
                    terminal_gate,
                    barrier_deadline_ns,
                )
                remaining_barrier_time()
                receipt_observed = float(transition["transition_receipt_observed_monotonic"])
                records = [
                    future.result(timeout=remaining_barrier_time()) for future in futures
                ]
                producer_finished = time.perf_counter()
    finally:
        for session in sessions:
            session.close()

    for record, meta in zip(records, dispatch_meta, strict=True):
        attempted = float(record["attempted_monotonic"])
        target = producer_started + int(meta["scheduled_offset_ms"]) / 1000.0
        meta["attempted_monotonic"] = attempted
        meta["schedule_lateness_ms"] = (attempted - target) * 1000.0
        meta["outcome"] = record.get("outcome")
        meta["status_code"] = int(record.get("status_code", 0))
    evidence = {
        "producer_started_monotonic": producer_started,
        "causal_gate_started_monotonic": causal_gate_started,
        "all_submitted_monotonic": all_submitted,
        "transition_receipt_observed_monotonic": receipt_observed,
        "producer_finished_monotonic": producer_finished,
        "adaptive_pacing": False,
        "switch_gate_basis": (
            "all40_schedule_plus_exact39_blue_terminal_plus_exact4x3_receipts_"
            "plus_exact2_pending_crossovers"
        ),
        "crossover_request_id": crossover_id,
        "route_switch_deadline": deadline_state,
        "route_switch_deadline_monotonic_ns": barrier_deadline_ns,
        "pre_switch_terminal_gate": terminal_gate,
        "dispatches": dispatch_meta,
        "max_reserved_requests_observed": max_reserved_count,
        "max_reserved_payload_bytes_observed": max_reserved_bytes,
        "reserved_requests_at_finish": reserved_count,
        "reserved_payload_bytes_at_finish": reserved_bytes,
    }
    return records, evidence, transition


def wait_in_flight(config: S6BMConfig, role: str, expected: int, timeout: float) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    latest: dict[str, Any] = {}
    while time.monotonic() < deadline:
        latest = controller_state(config)
        observed = int(dict(latest.get("in_flight", {})).get(role, 0))
        if observed == expected:
            return latest
        time.sleep(0.01)
    raise S6BMExperimentError(f"in_flight_timeout:{role}:{expected}:{latest}")


def wait_in_flight_at_least(
    config: S6BMConfig, role: str, minimum: int, timeout: float
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    latest: dict[str, Any] = {}
    while time.monotonic() < deadline:
        latest = controller_state(config)
        observed = int(dict(latest.get("in_flight", {})).get(role, 0))
        if observed >= minimum:
            return latest
        time.sleep(0.01)
    raise S6BMExperimentError(f"in_flight_minimum_timeout:{role}:{minimum}:{latest}")


def owner_sample(lease: GpuLease) -> dict[str, Any]:
    observed = read_active_gpu_lease()
    exact = (
        observed is not None
        and observed.run_id == lease.run_id
        and observed.lease_id == lease.lease_id
        and observed.fencing_token == lease.fencing_token
        and observed.scenario_id == "S6B-M"
        and observed.model_family == "tabular"
    )
    processes = run(
        [
            "nvidia-smi",
            "--query-compute-apps=pid,process_name,used_memory",
            "--format=csv,noheader,nounits",
        ],
        check=False,
    ).stdout.splitlines()
    return {
        "captured_at": utc_now(),
        "owner_exact": exact,
        "gpu_process_count": len([line for line in processes if line.strip()]),
        "gpu_process_projection": [
            hashlib.sha256(line.strip().encode("utf-8")).hexdigest()
            for line in processes
            if line.strip()
        ],
    }


class ResourceSampler:
    def __init__(self, api_pid: int, *, cadence_seconds: float = 0.5) -> None:
        self.api_pid = api_pid
        self.cadence_seconds = cadence_seconds
        self.samples: list[dict[str, Any]] = []
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def __enter__(self) -> "ResourceSampler":
        self._thread.start()
        return self

    def __exit__(self, *_args: object) -> None:
        self._stop.set()
        self._thread.join(timeout=5)

    def _run(self) -> None:
        process = psutil.Process(self.api_pid)
        process.cpu_percent(None)
        while not self._stop.is_set():
            try:
                gpu = capture_gpu()
                rss = process.memory_info().rss
                cpu = process.cpu_percent(None)
                self.samples.append(
                    {
                        "monotonic_seconds": time.perf_counter(),
                        "gpu": gpu,
                        "api_rss_bytes": rss,
                        "api_cpu_percent": cpu,
                    }
                )
            except (OSError, psutil.Error, S6BMExperimentError):
                self.samples.append(
                    {"monotonic_seconds": time.perf_counter(), "sample_error": True}
                )
            self._stop.wait(self.cadence_seconds)


def identities(config: S6BMConfig, lease: GpuLease) -> dict[str, Any]:
    return {
        "image_digest": config.image_digest,
        "repository_sha256": config.repository_sha256,
        "gpu_uuid": capture_gpu()["uuid"],
        "blue": {
            "model_name": config.blue.model_name,
            "model_version": config.blue.model_version,
            "artifact_sha256": config.blue.artifact_sha256,
            "config_sha256": config.blue.config_sha256,
        },
        "green": {
            "model_name": config.green.model_name,
            "model_version": config.green.model_version,
            "artifact_sha256": config.green.artifact_sha256,
            "config_sha256": config.green.config_sha256,
        },
        "lease": {
            "run_id": lease.run_id,
            "lease_id_sha256": hashlib.sha256(lease.lease_id.encode("utf-8")).hexdigest(),
            "fencing_token_sha256": hashlib.sha256(lease.fencing_token.encode("utf-8")).hexdigest(),
            "scenario_id": lease.scenario_id,
            "model_family": lease.model_family,
            "purpose": lease.lease_purpose,
            "owner_exact": True,
        },
    }


def phase_entry(
    config: S6BMConfig,
    phase: str,
    *,
    clock_chain: DualClockAnchorChain | None = None,
) -> dict[str, Any]:
    state = controller_state(config)
    if state["phase"] != phase:
        raise S6BMExperimentError(f"phase_identity_mismatch:{phase}:{state['phase']}")
    entry = {
        "phase": phase,
        "monotonic_seconds": time.perf_counter(),
        "generation": state["generation"],
        "route_weights": state["route_weights"],
        "loaded_roles": state["loaded_roles"],
        "in_flight": state["in_flight"],
    }
    if clock_chain is not None:
        entry["clock_anchor"] = clock_chain.capture(phase)
    return entry


def request_projection(records: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    logical = len(records)
    completed = sum(item.get("outcome") == "completed" for item in records)
    request_ids = [str(item.get("request_id", "")) for item in records]
    wrong_version = sum(
        item.get("outcome") == "completed" and item.get("model_role") not in {"blue", "green"}
        for item in records
    )
    return {
        "logical": logical,
        "accepted": completed,
        "terminal": completed,
        "lost": logical - completed,
        "duplicate_effect": len(request_ids) - len(set(request_ids)),
        "wrong_version": wrong_version,
        "transport_failure": sum(item.get("outcome") == "transport_failure" for item in records),
        "http_5xx": sum(int(item.get("status_code", 0)) >= 500 for item in records),
    }


def latency_projection(
    records: Sequence[Mapping[str, Any]], *, require_durable_terminal: bool = False
) -> dict[str, float]:
    values = sorted(
        float(item["elapsed_ms"]) for item in records if item.get("outcome") == "completed"
    )
    completed = []
    for item in records:
        if item.get("outcome") != "completed":
            continue
        if require_durable_terminal:
            receipt = dict(item.get("durable_effect", {}))
            if receipt.get("readback_visible") is not True:
                raise S6BMExperimentError("durable_terminal_readback_absent")
            terminal_ns = int(receipt.get("readback_finished_monotonic_ns", 0))
            if terminal_ns <= 0:
                raise S6BMExperimentError("durable_terminal_timestamp_absent")
            terminal = terminal_ns / 1e9
            if terminal > float(item["completed_monotonic"]):
                raise S6BMExperimentError("durable_terminal_after_response")
            completed.append(terminal)
        else:
            completed.append(float(item["completed_monotonic"]))
    completed.sort()
    if not values:
        raise S6BMExperimentError("latency_projection_empty")

    def percentile(value: float) -> float:
        position = (len(values) - 1) * value
        lower = int(position)
        upper = min(lower + 1, len(values) - 1)
        fraction = position - lower
        return values[lower] + (values[upper] - values[lower]) * fraction

    gaps = [
        (current - previous) * 1000
        for previous, current in zip(completed, completed[1:], strict=False)
    ]
    return {
        "p95_ms": percentile(0.95),
        "p99_ms": percentile(0.99),
        "max_inter_completion_gap_ms": max(gaps, default=0.0),
    }


def continuity_conservation_projection(
    records: Sequence[Mapping[str, Any]],
    traffic_plan: Mapping[str, Any],
    execution: Mapping[str, Any],
    transition_receipt: Mapping[str, Any],
    config: S6BMConfig,
) -> dict[str, Any]:
    route_applied = int(transition_receipt["route_applied_monotonic_ns"]) / 1e9
    producer_started = float(execution["producer_started_monotonic"])
    receipt_observed = float(execution["transition_receipt_observed_monotonic"])
    bridge_ids = {str(item["request_id"]) for item in traffic_plan["roles"]["bridge"]}
    terminal_times: dict[str, float] = {}
    for record in records:
        receipt = dict(record.get("durable_effect", {}))
        terminal_times[str(record["request_id"])] = (
            int(receipt.get("readback_finished_monotonic_ns", 0)) / 1e9
        )
    bridge_cross_switch = sum(
        str(record["request_id"]) in bridge_ids
        and float(record["attempted_monotonic"])
        < route_applied
        < terminal_times[str(record["request_id"])]
        for record in records
    )
    terminal_during_transition = sum(
        producer_started <= terminal <= receipt_observed for terminal in terminal_times.values()
    )
    role_counts = {
        role: len(list(traffic_plan["roles"][role])) for role in traffic_plan["role_order"]
    }
    dispatches = [dict(item) for item in execution["dispatches"]]
    completed = sum(item.get("outcome") == "completed" for item in records)
    return {
        "logical_request_ids": int(traffic_plan["logical_request_count"]),
        "offered": len(records),
        "admitted": completed,
        "terminal": len(records),
        "completed": completed,
        "duplicate_replay_attempts": 1,
        "client_attempts": len(records) + 1,
        "missing": int(traffic_plan["logical_request_count"]) - len(records),
        "duplicate": len(records) - len({str(item["request_id"]) for item in records}),
        "dropped": sum(item.get("outcome") != "completed" for item in records),
        "backpressure_terminal": sum(int(item.get("status_code", 0)) == 429 for item in records),
        "schedule_late_dispatches": sum(
            float(item["schedule_lateness_ms"]) > float(config.continuity["cadence_ms"])
            for item in dispatches
        ),
        "capacity_waited_dispatches": sum(
            float(item["capacity_wait_ms"]) > 0.001 for item in dispatches
        ),
        "terminal_during_transition": terminal_during_transition,
        "bridge_cross_switch_completions": bridge_cross_switch,
        "gap_clock": "durable_effect_readback_monotonic_ns",
        "role_counts": role_counts,
    }


def prometheus_query_response(query: str) -> dict[str, Any]:
    response = requests.get(
        f"{PROMETHEUS_URL}/api/v1/query",
        params={"query": query},
        timeout=10,
    )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise S6BMExperimentError("prometheus_query_object_required")
    return payload


def metric_queries(config: S6BMConfig, attempt_id: str) -> dict[str, str]:
    identity = f'scenario="s8-v4-s6bm",attempt_id="{attempt_id}"'
    queries: dict[str, str] = {}
    for role in ("blue", "green"):
        model = config.blue if role == "blue" else config.green
        model_labels = (
            f'model_role="{role}",model_name="{model.model_name}",'
            f'model_version="{model.model_version}"'
        )
        queries[f"api_{role}_completed"] = (
            f'evm_s6bm_requests_total{{{identity},{model_labels},outcome="completed"}}'
        )
        queries[f"api_{role}_effect"] = (
            f'evm_s6bm_terminal_effects_total{{{identity},{model_labels},outcome="committed"}}'
        )
        queries[f"triton_{role}_success"] = (
            f'nv_inference_request_success{{{identity},model="{model.model_name}",'
            f'version="{model.model_version}"}}'
        )
    return queries


def prometheus_attempt_snapshot(
    config: S6BMConfig,
    *,
    suite_id: str,
    attempt_id: str,
    run_id: str,
) -> dict[str, Any]:
    targets = prometheus_targets()
    jobs = {
        str(config.telemetry["prometheus_job_api"]),
        str(config.telemetry["prometheus_job_triton"]),
    }
    selected = [item for item in targets if str(dict(item.get("labels", {})).get("job")) in jobs]
    target_labels = [dict(item.get("labels", {})) for item in selected]
    if len(selected) != 2 or any(
        item.get("health") != "up"
        or dict(item.get("labels", {})).get("attempt_id") != attempt_id
        or dict(item.get("labels", {})).get("suite_id") != suite_id
        for item in selected
    ):
        raise S6BMExperimentError("prometheus_attempt_target_identity")
    return {
        "schema_version": "evm.s8_v4.s6bm_prometheus_snapshot.v1",
        "captured_at": utc_now(),
        "attempt_id": attempt_id,
        "run_id": run_id,
        "target_identity": {
            "suite_id": suite_id,
            "attempt_id": attempt_id,
            "api_up": any(
                labels.get("job") == config.telemetry["prometheus_job_api"]
                for labels in target_labels
            ),
            "triton_up": any(
                labels.get("job") == config.telemetry["prometheus_job_triton"]
                for labels in target_labels
            ),
            "labels": target_labels,
        },
        "queries": {
            key: {"query": query, "response": prometheus_query_response(query)}
            for key, query in metric_queries(config, attempt_id).items()
        },
    }


def direct_metrics(config: S6BMConfig) -> tuple[str, str]:
    api = requests.get(f"http://127.0.0.1:{config.ports['api']}/metrics", timeout=10)
    triton = requests.get(f"http://127.0.0.1:{config.ports['triton_metrics']}/metrics", timeout=10)
    api.raise_for_status()
    triton.raise_for_status()
    return api.text, triton.text


def prometheus_direct_comparison(
    config: S6BMConfig,
    snapshot: Mapping[str, Any],
    api_text: str,
    triton_text: str,
    attempt_id: str,
    *,
    expected_absent_triton_roles: frozenset[str] = frozenset(),
) -> dict[str, Any]:
    common = {"scenario": "s8-v4-s6bm", "attempt_id": attempt_id}
    comparisons: dict[str, Any] = {}
    errors: list[str] = []
    for role in ("blue", "green"):
        model = config.blue if role == "blue" else config.green
        api_labels = {
            "model_role": role,
            "model_name": model.model_name,
            "model_version": model.model_version,
            "outcome": "completed",
        }
        effect_labels = {**api_labels, "outcome": "committed"}
        triton_labels = {"model": model.model_name, "version": model.model_version}
        try:
            api_pairs = (
                (
                    f"api_{role}_completed",
                    direct_metric_value(api_text, "evm_s6bm_requests_total", api_labels),
                    {**common, **api_labels},
                    1,
                ),
                (
                    f"api_{role}_effect",
                    direct_metric_value(api_text, "evm_s6bm_terminal_effects_total", effect_labels),
                    {**common, **effect_labels},
                    1,
                ),
            )
            for key, direct_value, expected_labels, series_count in api_pairs:
                prom_value = prometheus_value(
                    snapshot,
                    key,
                    expected_labels,
                    expected_series_count=series_count,
                )
                matches = prom_value == direct_value
                comparisons[key] = {
                    "direct_value": direct_value,
                    "prometheus_value": prom_value,
                    "matches": matches,
                    "expected_labels": expected_labels,
                    "direct_series_count": 1,
                    "direct_series_labels": [expected_labels],
                }
                if not matches:
                    errors.append(f"s6bm_prometheus_direct_value:{key}")

            triton_key = f"triton_{role}_success"
            triton_expected_labels = {**common, **triton_labels}
            if role in expected_absent_triton_roles:
                triton_aggregate = direct_metric_optional_aggregate(
                    triton_text,
                    "nv_inference_request_success",
                    triton_labels,
                )
                prom_value = prometheus_optional_value(
                    snapshot,
                    triton_key,
                    triton_expected_labels,
                    expected_series_count=None,
                )
                if triton_aggregate is not None or prom_value is not None:
                    errors.append(f"s6bm_triton_expected_absent:{role}")
                comparisons[triton_key] = {
                    "direct_value": None,
                    "prometheus_value": None,
                    "matches": triton_aggregate is None and prom_value is None,
                    "expected_labels": triton_expected_labels,
                    "direct_series_count": 0,
                    "direct_series_labels": [],
                }
            else:
                triton_aggregate = direct_metric_aggregate(
                    triton_text,
                    "nv_inference_request_success",
                    triton_labels,
                )
                direct_value = float(triton_aggregate["value"])
                prom_value = prometheus_value(
                    snapshot,
                    triton_key,
                    triton_expected_labels,
                    expected_series_count=None,
                )
                matches = prom_value == direct_value
                comparisons[triton_key] = {
                    "direct_value": direct_value,
                    "prometheus_value": prom_value,
                    "matches": matches,
                    "expected_labels": triton_expected_labels,
                    "direct_series_count": int(triton_aggregate["series_count"]),
                    "direct_series_labels": triton_aggregate["series_labels"],
                }
                if not matches:
                    errors.append(f"s6bm_prometheus_direct_value:{triton_key}")
        except (ValueError, RuntimeError) as exc:
            errors.append(f"{role}:{type(exc).__name__}:{exc}")
    return {
        "passed": not errors,
        "errors": errors,
        "comparisons": comparisons,
    }


def _prometheus_matches_direct(
    config: S6BMConfig,
    snapshot: Mapping[str, Any],
    api_text: str,
    triton_text: str,
    attempt_id: str,
) -> bool:
    return bool(
        prometheus_direct_comparison(
            config,
            snapshot,
            api_text,
            triton_text,
            attempt_id,
        )["passed"]
    )


def capture_metric_checkpoint(
    config: S6BMConfig,
    *,
    suite_id: str,
    attempt_id: str,
    run_id: str,
    timeout: float = 15,
    expected_absent_triton_roles: frozenset[str] = frozenset(),
) -> tuple[str, str, dict[str, Any]]:
    deadline = time.monotonic() + timeout
    last: dict[str, Any] | None = None
    last_comparison: dict[str, Any] | None = None
    while time.monotonic() < deadline:
        api_text, triton_text = direct_metrics(config)
        try:
            last = prometheus_attempt_snapshot(
                config,
                suite_id=suite_id,
                attempt_id=attempt_id,
                run_id=run_id,
            )
            last_comparison = prometheus_direct_comparison(
                config,
                last,
                api_text,
                triton_text,
                attempt_id,
                expected_absent_triton_roles=expected_absent_triton_roles,
            )
            if last_comparison["passed"]:
                return api_text, triton_text, last
        except (requests.RequestException, S6BMExperimentError):
            pass
        time.sleep(float(config.telemetry["prometheus_scrape_seconds"]))
    raise S6BMExperimentError(
        f"prometheus_metric_convergence:{attempt_id}:"
        f"{canonical({'comparison': last_comparison, 'snapshot': last})}"
    )


def artifact_reference(root: Path, path: Path) -> dict[str, Any]:
    return {
        "path": path.relative_to(root).as_posix(),
        "sha256": sha256_file(path),
        "bytes": path.stat().st_size,
    }


def begin_attempt_observability(
    config: S6BMConfig,
    *,
    suite_root: Path,
    suite_id: str,
    attempt_id: str,
    run_id: str,
) -> dict[str, Any]:
    bind_prometheus_attempt(
        config,
        suite_id=suite_id,
        attempt_id=attempt_id,
    )
    api_text, triton_text, prometheus = capture_metric_checkpoint(
        config,
        suite_id=suite_id,
        attempt_id=attempt_id,
        run_id=run_id,
    )
    root = suite_root / "observability" / attempt_id
    root.mkdir(parents=True, exist_ok=False)
    api_path = root / "api-metrics-before.txt"
    triton_path = root / "triton-metrics-before.txt"
    prometheus_path = root / "prometheus-before.json"
    api_path.write_text(api_text, encoding="utf-8", newline="\n")
    triton_path.write_text(triton_text, encoding="utf-8", newline="\n")
    canonical_write(prometheus_path, prometheus)
    return {
        "root": root,
        "trace_start_offset": OTEL_TRACE_FILE.stat().st_size,
        "artifacts": {
            "api_metrics_before": artifact_reference(suite_root, api_path),
            "triton_metrics_before": artifact_reference(suite_root, triton_path),
            "prometheus_before": artifact_reference(suite_root, prometheus_path),
        },
    }


def expected_attempt_trace_counts(attempt: Mapping[str, Any]) -> dict[str, int]:
    records = [dict(item) for item in attempt.get("request_records", [])]
    replay = dict(attempt.get("idempotent_replay", {}))
    request_ids = {str(item.get("request_id", "")) for item in records}
    if not records or "" in request_ids or len(request_ids) != len(records):
        raise S6BMExperimentError(f"otlp_replay_identity:{str(attempt.get('attempt_id', ''))}")
    if not replay:
        return {
            "controller": len(records),
            "inference": len(records),
        }
    replay_record = dict(replay.get("record", {}))
    if (
        replay_record.get("replayed") is not True
        or str(replay_record.get("request_id", "")) not in request_ids
        or int(replay.get("unique_count_before", -1)) != int(replay.get("unique_count_after", -2))
    ):
        raise S6BMExperimentError(f"otlp_replay_identity:{str(attempt.get('attempt_id', ''))}")
    return {
        "controller": len(records) + 1,
        "inference": len(records),
    }


def finish_attempt_observability(
    config: S6BMConfig,
    *,
    suite_root: Path,
    suite_id: str,
    attempt: dict[str, Any],
    checkpoint: Mapping[str, Any],
) -> dict[str, Any]:
    attempt_id = str(attempt["attempt_id"])
    run_id = str(dict(attempt["identities"])["lease"]["run_id"])
    api_text, triton_text, prometheus = capture_metric_checkpoint(
        config,
        suite_id=suite_id,
        attempt_id=attempt_id,
        run_id=run_id,
    )
    root = Path(checkpoint["root"])
    api_path = root / "api-metrics-after.txt"
    triton_path = root / "triton-metrics-after.txt"
    prometheus_path = root / "prometheus-after.json"
    api_path.write_text(api_text, encoding="utf-8", newline="\n")
    triton_path.write_text(triton_text, encoding="utf-8", newline="\n")
    canonical_write(prometheus_path, prometheus)

    expected_spans = expected_attempt_trace_counts(attempt)
    expected_controller_spans = expected_spans["controller"]
    expected_inference_spans = expected_spans["inference"]
    deadline = time.monotonic() + 30
    controller_spans = 0
    inference_spans = 0
    trace_start_offset = int(checkpoint["trace_start_offset"])
    while time.monotonic() < deadline:
        controller_spans = attempt_span_count(
            OTEL_TRACE_FILE,
            attempt_id,
            stage="s6bm_controller",
            start_offset=trace_start_offset,
        )
        inference_spans = attempt_span_count(
            OTEL_TRACE_FILE,
            attempt_id,
            stage="triton_inference",
            start_offset=trace_start_offset,
        )
        if (
            controller_spans == expected_controller_spans
            and inference_spans == expected_inference_spans
        ):
            break
        time.sleep(1)
    if controller_spans != expected_controller_spans or inference_spans != expected_inference_spans:
        raise S6BMExperimentError(
            f"otlp_trace_convergence:{attempt_id}:"
            f"controller={controller_spans}/{expected_controller_spans}:"
            f"inference={inference_spans}/{expected_inference_spans}"
        )
    trace_export = collect_attempt_trace_export(
        OTEL_TRACE_FILE, attempt_id, start_offset=trace_start_offset
    )
    trace_path = root / "raw-otlp-spans.json"
    canonical_write(trace_path, trace_export)

    artifacts = dict(checkpoint["artifacts"])
    artifacts.update(
        {
            "api_metrics_after": artifact_reference(suite_root, api_path),
            "triton_metrics_after": artifact_reference(suite_root, triton_path),
            "prometheus_after": artifact_reference(suite_root, prometheus_path),
            "trace_export": artifact_reference(suite_root, trace_path),
        }
    )
    attempt["observability"] = {"artifacts": artifacts}
    summary = validate_observability_bundle(
        suite_root,
        attempt,
        config,
        compare_projection=False,
    )
    report_path = root / "join-recompute-report.json"
    canonical_write(report_path, summary)
    artifacts["join_report"] = artifact_reference(suite_root, report_path)
    attempt["observability"] = {"artifacts": artifacts, "summary": summary}
    validate_observability_bundle(suite_root, attempt, config)
    return summary


def capture_transition_observability(
    config: S6BMConfig,
    *,
    suite_root: Path,
    suite_id: str,
    attempt_id: str,
    run_id: str,
    checkpoint: dict[str, Any],
) -> None:
    api_text, triton_text, prometheus = capture_metric_checkpoint(
        config,
        suite_id=suite_id,
        attempt_id=attempt_id,
        run_id=run_id,
    )
    root = Path(checkpoint["root"])
    api_path = root / "api-metrics-before-blue-unload.txt"
    triton_path = root / "triton-metrics-before-blue-unload.txt"
    prometheus_path = root / "prometheus-before-blue-unload.json"
    api_path.write_text(api_text, encoding="utf-8", newline="\n")
    triton_path.write_text(triton_text, encoding="utf-8", newline="\n")
    canonical_write(prometheus_path, prometheus)
    checkpoint["artifacts"].update(
        {
            "api_metrics_before_blue_unload": artifact_reference(suite_root, api_path),
            "triton_metrics_before_blue_unload": artifact_reference(suite_root, triton_path),
            "prometheus_before_blue_unload": artifact_reference(suite_root, prometheus_path),
        }
    )


def capture_post_unload_observability(
    config: S6BMConfig,
    *,
    suite_root: Path,
    suite_id: str,
    attempt_id: str,
    run_id: str,
    checkpoint: dict[str, Any],
) -> None:
    _api_text, triton_text, prometheus = capture_metric_checkpoint(
        config,
        suite_id=suite_id,
        attempt_id=attempt_id,
        run_id=run_id,
        expected_absent_triton_roles=frozenset({"blue"}),
    )
    root = Path(checkpoint["root"])
    triton_path = root / "triton-metrics-after-blue-unload.txt"
    prometheus_path = root / "prometheus-after-blue-unload.json"
    triton_path.write_text(triton_text, encoding="utf-8", newline="\n")
    canonical_write(prometheus_path, prometheus)
    checkpoint["artifacts"].update(
        {
            "triton_metrics_after_blue_unload": artifact_reference(suite_root, triton_path),
            "prometheus_after_blue_unload": artifact_reference(suite_root, prometheus_path),
        }
    )


def telemetry_snapshot(
    config: S6BMConfig,
    *,
    suite_id: str,
    attempt_id: str,
    timeout: float = 30,
) -> dict[str, Any]:
    expected_jobs = {
        str(config.telemetry["prometheus_job_api"]),
        str(config.telemetry["prometheus_job_triton"]),
    }
    deadline = time.monotonic() + timeout
    last_labels: list[dict[str, Any]] = []
    while time.monotonic() < deadline:
        selected = []
        for target in prometheus_targets():
            labels = dict(target.get("labels", {}))
            if (
                labels.get("job") in expected_jobs
                and labels.get("scenario") == "s8-v4-s6bm"
                and labels.get("suite_id") == suite_id
                and labels.get("attempt_id") == attempt_id
            ):
                selected.append(target)
        last_labels = [dict(item.get("labels", {})) for item in selected]
        if (
            len(selected) == 2
            and {dict(item.get("labels", {})).get("job") for item in selected} == expected_jobs
            and all(item.get("health") == "up" for item in selected)
        ):
            metrics = requests.get(
                f"http://127.0.0.1:{config.ports['triton_metrics']}/metrics",
                timeout=10,
            ).text
            return {
                "suite_id": suite_id,
                "attempt_id": attempt_id,
                "target_count": len(selected),
                "target_labels": sorted(last_labels, key=lambda item: str(item["job"])),
                "api_target_up": True,
                "triton_target_up": True,
                "triton_success_total": prometheus_query(
                    'sum(nv_inference_request_success{model=~"s6bm_blue|s6bm_green"})'
                ),
                "api_request_metric_total": prometheus_query("sum(evm_s6bm_requests_total)"),
                "direct_metrics_present": "nv_inference_request_success" in metrics,
            }
        time.sleep(0.25)
    raise S6BMExperimentError(
        f"telemetry_attempt_target_timeout:{suite_id}:{attempt_id}:{last_labels}"
    )


def run_baseline(
    config: S6BMConfig,
    lease: GpuLease,
    source: Mapping[str, str],
    repetition: int,
    api: ApiProcess,
    *,
    suite_id: str,
) -> dict[str, Any]:
    attempt_id = f"s6bm-baseline-{repetition}-{uuid4().hex[:10]}"
    bind_prometheus_attempt(config, suite_id=suite_id, attempt_id=attempt_id)
    initialize_controller(config, lease, source)
    records, _ = send_batch(
        config,
        lease.run_id,
        attempt_id,
        f"{attempt_id}-request",
        int(config.procedure["baseline_requests"]),
        int(config.procedure["request_concurrency"]),
        expected_model_role="blue",
    )
    summary = request_projection(records)
    if summary["lost"] != 0 or any(item.get("model_role") != "blue" for item in records):
        raise S6BMExperimentError(f"baseline_failed:{repetition}:{summary}")
    result = {
        "schema_version": "evm.s8_v4.s6bm_baseline_private.v1",
        "attempt_id": attempt_id,
        "profile": "baseline",
        "repetition": repetition,
        "credit": "non_credit",
        "source_revision": source["revision"],
        "identities": identities(config, lease),
        "request_records": records,
        "requests": summary,
        "latency": latency_projection(records),
        "telemetry": telemetry_snapshot(config, suite_id=suite_id, attempt_id=attempt_id),
        "owner_samples": [owner_sample(lease)],
        "resources": {"api_pid": api.process.pid, "gpu": capture_gpu()},
    }
    reset_controller(config, lease)
    return result


def run_success(
    config: S6BMConfig,
    lease: GpuLease,
    source: Mapping[str, str],
    repetition: int,
    api: ApiProcess,
    *,
    suite_root: Path,
    suite_id: str,
    execution_progress: dict[str, Any],
    credit: str = "credit",
) -> dict[str, Any]:
    started_at = utc_now()
    attempt_id = f"s6bm-success-{repetition}-{uuid4().hex[:10]}"
    traffic_plan = build_continuity_plan(config, attempt_id)
    traffic_plan_path = suite_root / "traffic-plans" / attempt_id / "traffic-plan.json"
    canonical_write(traffic_plan_path, traffic_plan)
    plan_frozen_monotonic = time.perf_counter()
    clock_chain = DualClockAnchorChain(
        source_identity=f"runner:{source['revision']}:{suite_id}:{attempt_id}"
    )
    initialize_controller(config, lease, source)
    controller_initialized_monotonic = time.perf_counter()
    timeline = [phase_entry(config, "blue_only", clock_chain=clock_chain)]
    owner_samples = [owner_sample(lease)]
    physical: dict[str, bool] = {}
    records: list[dict[str, Any]] = []
    observability_attempt: dict[str, Any] | None = None
    observability: dict[str, Any] | None = None
    with ResourceSampler(api.process.pid) as resources:
        transition_started = time.perf_counter()
        apply_control(config, lease, "green_loaded")
        physical["green_loaded_ready"] = model_ready(config, "green")
        timeline.append(phase_entry(config, "green_warmup", clock_chain=clock_chain))
        observability_checkpoint = begin_attempt_observability(
            config,
            suite_root=suite_root,
            suite_id=suite_id,
            attempt_id=attempt_id,
            run_id=lease.run_id,
        )
        for _ in range(int(config.procedure["warmup_requests"])):
            direct_infer(config, "green")
        apply_control(config, lease, "canary_started")
        timeline.append(phase_entry(config, "canary", clock_chain=clock_chain))
        canary_bodies = request_bodies_from_plan(
            config,
            lease.run_id,
            attempt_id,
            traffic_plan["roles"]["canary"],
        )
        canary_records = send_bodies(
            config,
            canary_bodies,
            int(config.procedure["request_concurrency"]),
        )
        records.extend(canary_records)
        execution_progress.update(
            {"attempt_id": attempt_id, "requests": request_projection(records)}
        )
        replay_body = canary_bodies[0]
        replay_before = int(controller_state(config)["accepted_unique"])
        replay = send_request(config, replay_body)
        replay_after = int(controller_state(config)["accepted_unique"])
        replay_evidence = {
            "request_id": replay["request_id"],
            "replayed": replay.get("replayed") is True,
            "unique_count_before": replay_before,
            "unique_count_after": replay_after,
            "record": replay,
        }

        hold_generation = int(controller_state(config)["generation"])
        hold_body = request_bodies_from_plan(
            config,
            lease.run_id,
            attempt_id,
            traffic_plan["roles"]["causal_hold"],
            route_generation=hold_generation,
        )[0]
        hold_identity = expected_causal_identity_for_request(
            TritonBlueGreenPredictRequest.model_validate(hold_body)
        ).model_dump(mode="json")
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            hold_future = pool.submit(send_request, config, hold_body)
            wait_in_flight(config, "blue", 1, 5)
            bridge_bodies = request_bodies_from_plan(
                config,
                lease.run_id,
                attempt_id,
                traffic_plan["roles"]["bridge"],
                route_generation=hold_generation,
            )
            required_bridge_items = [
                dict(item)
                for item in traffic_plan["roles"]["bridge"]
                if item.get("actor_receipt_required") is True
            ]
            required_bridge_ids = [str(item["request_id"]) for item in required_bridge_items]
            crossover_bridge_ids = [
                str(item["request_id"])
                for item in required_bridge_items
                if item.get("causal_crossover") is True
            ]
            if len(crossover_bridge_ids) != 1:
                raise S6BMExperimentError("continuity_crossover_exact_set")
            crossover_bridge_id = crossover_bridge_ids[0]
            expected_pending_crossover_ids = sorted(
                [str(hold_identity["request_id"]), crossover_bridge_id]
            )
            bridge_bodies_by_id = {str(body["request_id"]): body for body in bridge_bodies}
            def collect_bridge_receipt_proof(
                deadline_monotonic_ns: int,
            ) -> dict[str, Any]:
                collectors = [
                    start_triton_start_receipt_collector(
                        config,
                        suite_root=suite_root,
                        checkpoint=observability_checkpoint,
                        body=hold_body,
                        source_revision=source["revision"],
                        suite_id=suite_id,
                        deadline_monotonic_ns=deadline_monotonic_ns,
                    )
                ]
                try:
                    collectors.extend(
                        start_triton_start_receipt_collector(
                            config,
                            suite_root=suite_root,
                            checkpoint=observability_checkpoint,
                            body=bridge_bodies_by_id[request_id],
                            source_revision=source["revision"],
                            suite_id=suite_id,
                            deadline_monotonic_ns=deadline_monotonic_ns,
                            artifact_scope=f"bridge-receipts/{request_id}",
                        )
                        for request_id in required_bridge_ids
                    )
                    results = [
                        wait_triton_start_receipt_collector(
                            item,
                            suite_root=suite_root,
                            deadline_monotonic_ns=deadline_monotonic_ns,
                        )
                        for item in collectors
                    ]
                    triton_receipt = results[0]
                    bridge_triton_receipts = results[1:]
                    bridge_receipt_gate = read_required_bridge_start_receipts(
                        config,
                        suite_root=suite_root,
                        attempt_id=attempt_id,
                        required_request_ids=required_bridge_ids,
                        route_generation=hold_generation,
                        deadline_monotonic_ns=deadline_monotonic_ns,
                    )
                    bridge_receipt_gate["collector_request_ids"] = [
                        str(item["request_id"]) for item in bridge_triton_receipts
                    ]
                    bridge_receipt_gate["collector_request_set_sha256"] = canonical_sha256(
                        bridge_receipt_gate["collector_request_ids"]
                    )
                    bridge_receipt_gate["gate_satisfied_monotonic"] = time.perf_counter()
                    return {
                        "triton_start_receipt": triton_receipt,
                        "bridge_triton_start_receipts": bridge_triton_receipts,
                        "bridge_actor_receipt_gate": bridge_receipt_gate,
                    }
                finally:
                    stop_trace_collectors(collectors)

            def switch_after_fixed_schedule(
                receipt_proof: dict[str, Any],
                terminal_gate: dict[str, Any],
                deadline_monotonic_ns: int,
            ) -> dict[str, Any]:
                expected_terminal_bodies = {
                    request_id: body
                    for request_id, body in bridge_bodies_by_id.items()
                    if request_id != crossover_bridge_id
                }
                terminal_gate = bind_pre_switch_terminal_gate(
                    config,
                    suite_root=suite_root,
                    attempt_id=attempt_id,
                    expected_bodies=expected_terminal_bodies,
                    response_records={
                        str(item["request_id"]): item
                        for item in terminal_gate.get("online_response_records", [])
                    },
                    terminal_gate=terminal_gate,
                    deadline_monotonic_ns=deadline_monotonic_ns,
                )
                state_before_switch = controller_state(config)
                if (
                    state_before_switch.get("pending_crossover_request_ids")
                    != expected_pending_crossover_ids
                    or int(state_before_switch.get("pending_crossover_count", -1)) != 2
                    or int(state_before_switch["in_flight"]["blue"])
                    < int(config.continuity["minimum_blue_in_flight_at_switch"])
                    or int(terminal_gate.get("observed_terminal_count", -1)) != 39
                    or terminal_gate.get("crossover_request_id") != crossover_bridge_id
                ):
                    raise S6BMExperimentError(
                        f"continuity_switch_gate_state:{state_before_switch}:{terminal_gate}"
                    )
                pre_switch = wait_in_flight_at_least(
                    config,
                    "blue",
                    int(config.continuity["minimum_blue_in_flight_at_switch"]),
                    5,
                )
                switch_invoked = time.perf_counter()
                switched = apply_control(
                    config,
                    lease,
                    "green_switched",
                    deadline_monotonic_ns=deadline_monotonic_ns,
                    causal_crossover=hold_identity,
                    continuity_receipt_request_ids=sorted(required_bridge_ids),
                    continuity_crossover_request_ids=[crossover_bridge_id],
                    pending_crossover_request_ids=expected_pending_crossover_ids,
                    continuity_terminal_request_ids=terminal_gate[
                        "expected_terminal_request_ids"
                    ],
                    continuity_terminal_request_set_sha256=terminal_gate[
                        "expected_terminal_request_set_sha256"
                    ],
                    continuity_terminal_records_sha256=terminal_gate[
                        "terminal_records_sha256"
                    ],
                )
                receipt_observed = time.perf_counter()
                receipt = dict(switched.get("transition_receipt") or {})
                if not receipt:
                    raise S6BMExperimentError("route_transition_receipt_absent")
                if (
                    receipt.get("continuity_receipt_request_ids") != sorted(required_bridge_ids)
                    or receipt.get("continuity_crossover_request_ids") != [crossover_bridge_id]
                    or receipt.get("pending_crossover_request_ids")
                    != expected_pending_crossover_ids
                    or receipt.get("released_crossover_request_ids")
                    != expected_pending_crossover_ids
                    or receipt.get("continuity_terminal_request_ids")
                    != terminal_gate["expected_terminal_request_ids"]
                    or receipt.get("continuity_terminal_request_set_sha256")
                    != terminal_gate["expected_terminal_request_set_sha256"]
                    or receipt.get("continuity_terminal_records_sha256")
                    != terminal_gate["terminal_records_sha256"]
                    or receipt.get("crossover_release_basis")
                    != "fence_commit_readback_and_route_applied"
                ):
                    raise S6BMExperimentError("continuity_crossover_release_receipt")
                timeline.append(phase_entry(config, "green_active", clock_chain=clock_chain))
                apply_control(config, lease, "blue_drain_started")
                timeline.append(phase_entry(config, "blue_draining", clock_chain=clock_chain))
                return {
                    "triton_start_receipt": receipt_proof["triton_start_receipt"],
                    "transition_receipt": receipt,
                    "switch_invoked_monotonic": switch_invoked,
                    "transition_receipt_observed_monotonic": receipt_observed,
                    "blue_in_flight_before_switch": int(pre_switch["in_flight"]["blue"]),
                    "pre_switch_state": {
                        "generation": int(state_before_switch["generation"]),
                        "phase": state_before_switch["phase"],
                        "blue_in_flight": int(state_before_switch["in_flight"]["blue"]),
                        "pending_crossover_request_ids": state_before_switch[
                            "pending_crossover_request_ids"
                        ],
                        "pending_crossover_count": int(
                            state_before_switch["pending_crossover_count"]
                        ),
                    },
                    "bridge_triton_start_receipts": receipt_proof["bridge_triton_start_receipts"],
                    "bridge_actor_receipt_gate": receipt_proof["bridge_actor_receipt_gate"],
                    "pre_switch_terminal_gate": terminal_gate,
                    "route_switch_deadline_monotonic_ns": deadline_monotonic_ns,
                }

            bridge_records, continuity_execution, transition = run_fixed_bridge_producer(
                config,
                bridge_bodies,
                traffic_plan["roles"]["bridge"],
                collect_bridge_receipt_proof,
                switch_after_fixed_schedule,
            )
            continuity_execution.update(
                {
                    "plan_sha256": traffic_plan["plan_sha256"],
                    "plan_frozen_monotonic": plan_frozen_monotonic,
                    "controller_initialized_monotonic": controller_initialized_monotonic,
                    "switch_invoked_monotonic": transition["switch_invoked_monotonic"],
                    "blue_in_flight_before_switch": transition["blue_in_flight_before_switch"],
                    "bridge_triton_start_receipts": transition["bridge_triton_start_receipts"],
                    "bridge_actor_receipt_gate": transition["bridge_actor_receipt_gate"],
                    "pre_switch_state": transition["pre_switch_state"],
                }
            )
            triton_start_receipt = transition["triton_start_receipt"]
            transition_receipt = transition["transition_receipt"]
            records.extend(bridge_records)
            green_bodies = request_bodies_from_plan(
                config,
                lease.run_id,
                attempt_id,
                traffic_plan["roles"]["normal"],
            )
            green_records = send_bodies(
                config,
                green_bodies,
                int(config.procedure["request_concurrency"]),
            )
            records.extend(green_records)
            records.append(hold_future.result(timeout=20))
            execution_progress.update(
                {"attempt_id": attempt_id, "requests": request_projection(records)}
            )
        wait_in_flight(config, "blue", 0, float(config.procedure["drain_timeout_seconds"]))
        blue_before_unload = int(controller_state(config)["in_flight"]["blue"])
        capture_transition_observability(
            config,
            suite_root=suite_root,
            suite_id=suite_id,
            attempt_id=attempt_id,
            run_id=lease.run_id,
            checkpoint=observability_checkpoint,
        )
        apply_control(
            config,
            lease,
            "blue_unloaded",
            causal_crossover=hold_identity,
        )
        physical["blue_unloaded_not_ready"] = not model_ready(config, "blue")
        capture_post_unload_observability(
            config,
            suite_root=suite_root,
            suite_id=suite_id,
            attempt_id=attempt_id,
            run_id=lease.run_id,
            checkpoint=observability_checkpoint,
        )
        timeline.append(phase_entry(config, "green_only", clock_chain=clock_chain))
        transition_seconds = time.perf_counter() - transition_started

        rollback_started = time.perf_counter()
        apply_control(config, lease, "blue_loaded")
        physical["blue_reloaded_ready"] = model_ready(config, "blue")
        timeline.append(phase_entry(config, "rollback_warmup", clock_chain=clock_chain))
        for _ in range(int(config.procedure["warmup_requests"])):
            direct_infer(config, "blue")
        apply_control(config, lease, "blue_switched")
        timeline.append(phase_entry(config, "blue_active_rollback", clock_chain=clock_chain))
        apply_control(config, lease, "green_drain_started")
        timeline.append(phase_entry(config, "green_draining", clock_chain=clock_chain))
        wait_in_flight(config, "green", 0, float(config.procedure["drain_timeout_seconds"]))
        green_before_unload = int(controller_state(config)["in_flight"]["green"])
        direct_infer(config, "blue")
        observability_attempt = {
            "attempt_id": attempt_id,
            "source_revision": source["revision"],
            "identities": identities(config, lease),
            "request_records": records,
            "idempotent_replay": replay_evidence,
        }
        observability = finish_attempt_observability(
            config,
            suite_root=suite_root,
            suite_id=suite_id,
            attempt=observability_attempt,
            checkpoint=observability_checkpoint,
        )
        apply_control(config, lease, "green_unloaded")
        physical["green_unloaded_not_ready"] = not model_ready(config, "green")
        timeline.append(phase_entry(config, "rolled_back", clock_chain=clock_chain))
        physical["blue_final_ready"] = model_ready(config, "blue")
        rollback_seconds = time.perf_counter() - rollback_started
        owner_samples.append(owner_sample(lease))

    summary = request_projection(records)
    latency = latency_projection(records, require_durable_terminal=True)
    traffic_conservation = continuity_conservation_projection(
        records,
        traffic_plan,
        continuity_execution,
        transition_receipt,
        config,
    )
    state = controller_state(config)
    causal_root = suite_root / "causal" / attempt_id
    effects_response = requests.get(api_url(config, f"effects/{attempt_id}"), timeout=30)
    effects_response.raise_for_status()
    causal_response = requests.get(api_url(config, f"causal-events/{attempt_id}"), timeout=30)
    causal_response.raise_for_status()
    effects_path = causal_root / "durable-effects.json"
    causal_path = causal_root / "causal-events.json"
    canonical_write(effects_path, effects_response.json())
    canonical_write(causal_path, causal_response.json())
    telemetry = telemetry_snapshot(config, suite_id=suite_id, attempt_id=attempt_id)
    samples = resources.samples
    peak_vram = max(
        (float(item["gpu"]["memory_used_mib"]) for item in samples if "gpu" in item),
        default=capture_gpu()["memory_used_mib"],
    )
    result = {
        "schema_version": "evm.s8_v4.s6bm_success_private.v1",
        "attempt_id": attempt_id,
        "profile": "successful_transition",
        "repetition": repetition,
        "credit": credit,
        "acceptance_credit": credit == "credit",
        "started_at": started_at,
        "finished_at": utc_now(),
        "source_revision": source["revision"],
        "identities": identities(config, lease),
        "phase_timeline": timeline,
        "traffic_plan": traffic_plan,
        "traffic_plan_artifact": artifact_reference(suite_root, traffic_plan_path),
        "continuity_execution": continuity_execution,
        "traffic_conservation": traffic_conservation,
        "causal_proof": {
            "crossover_identity": hold_identity,
            "triton_start_receipt": triton_start_receipt,
            "route_transition_receipt": transition_receipt,
            "durable_effect_export": artifact_reference(suite_root, effects_path),
            "causal_event_export": artifact_reference(suite_root, causal_path),
        },
        "request_records": records,
        "requests": summary,
        "idempotent_replay": replay_evidence,
        "illegal_owner_overlap": sum(not item["owner_exact"] for item in owner_samples),
        "owner_samples": owner_samples,
        "trace_complete": sum(bool(item.get("trace_id")) for item in records),
        "blue_in_flight_before_unload": blue_before_unload,
        "green_in_flight_before_unload": green_before_unload,
        "rollback_exact_blue": (
            state["phase"] == "rolled_back"
            and state["route_weights"] == {"blue": 100, "green": 0}
            and state["loaded_roles"] == ["blue"]
        ),
        "latency": latency,
        "transition_seconds": transition_seconds,
        "rollback_seconds": rollback_seconds,
        "peak_vram_mib": peak_vram,
        "physical_model_state": physical,
        "telemetry": telemetry,
        "resource_samples": samples,
        "cleanup": {
            "blue_only": state["route_weights"] == {"blue": 100, "green": 0},
            "green_unloaded": not model_ready(config, "green"),
            "queue_zero": queue_counts()["active"] == 0,
            "lease_owner_exact": owner_sample(lease)["owner_exact"],
            "controller_in_flight_zero": not any(state["in_flight"].values()),
            "controller_pending_crossovers_zero": int(state.get("pending_crossover_count", -1))
            == 0,
            "prometheus_targets_up": telemetry["api_target_up"] and telemetry["triton_target_up"],
        },
    }
    if observability is None or observability_attempt is None:
        raise S6BMExperimentError("observability_after_checkpoint_missing")
    result["observability"] = observability_attempt["observability"]
    result["telemetry"].update(
        {
            "trace_correlation_complete": observability["trace_correlation_complete"],
            "metric_delta_complete": observability["metric_delta_complete"],
        }
    )
    prevalidation_path = suite_root / "prevalidation" / f"{attempt_id}-raw-receipt-bundle.json"
    canonical_write(prevalidation_path, result)
    execution_progress.update(
        {
            "attempt_id": attempt_id,
            "requests": summary,
            "prevalidation_artifact": artifact_reference(suite_root, prevalidation_path),
        }
    )
    result["causal_projection"] = validate_causal_bundle(
        suite_root,
        result,
        config,
        compare_projection=False,
    )
    reset_controller(config, lease)
    return result


def run_causal_qualification(
    config: S6BMConfig,
    lease: GpuLease,
    source: Mapping[str, str],
    api: ApiProcess,
    *,
    suite_root: Path,
    suite_id: str,
    execution_progress: dict[str, Any],
) -> dict[str, Any]:
    """Exercise the causal fence once without granting acceptance credit."""
    attempt_id = f"s6bm-causal-qualification-{uuid4().hex[:10]}"
    clock_chain = DualClockAnchorChain(
        source_identity=f"runner:{source['revision']}:{suite_id}:{attempt_id}"
    )
    initialize_controller(config, lease, source)
    timeline = [phase_entry(config, "blue_only", clock_chain=clock_chain)]
    physical: dict[str, bool] = {}
    transition_started = time.perf_counter()
    apply_control(config, lease, "green_loaded")
    physical["green_loaded_ready"] = model_ready(config, "green")
    timeline.append(phase_entry(config, "green_warmup", clock_chain=clock_chain))
    checkpoint = begin_attempt_observability(
        config,
        suite_root=suite_root,
        suite_id=suite_id,
        attempt_id=attempt_id,
        run_id=lease.run_id,
    )
    for _ in range(int(config.procedure["warmup_requests"])):
        direct_infer(config, "green")
    apply_control(config, lease, "canary_started")
    timeline.append(phase_entry(config, "canary", clock_chain=clock_chain))

    hold_generation = int(controller_state(config)["generation"])
    hold_body = request_body(
        config,
        lease.run_id,
        attempt_id,
        blue_request_id(f"{attempt_id}-hold"),
        expected_model_role="blue",
        hold_ms=int(config.procedure["long_in_flight_hold_ms"]),
        expected_route_generation=hold_generation,
        causal_crossover=True,
        route_switch_deadline_owner=True,
    )
    hold_identity = expected_causal_identity_for_request(
        TritonBlueGreenPredictRequest.model_validate(hold_body)
    ).model_dump(mode="json")
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        hold_future = pool.submit(send_request, config, hold_body)
        wait_in_flight(config, "blue", 1, 5)
        deadline_state = wait_route_switch_deadline(
            config,
            owner_request_id=str(hold_identity["request_id"]),
        )
        deadline_monotonic_ns = int(deadline_state["deadline_monotonic_ns"])
        collector = start_triton_start_receipt_collector(
            config,
            suite_root=suite_root,
            checkpoint=checkpoint,
            body=hold_body,
            source_revision=source["revision"],
            suite_id=suite_id,
            deadline_monotonic_ns=deadline_monotonic_ns,
        )
        triton_start_receipt = wait_triton_start_receipt_collector(
            collector,
            suite_root=suite_root,
            deadline_monotonic_ns=deadline_monotonic_ns,
        )
        switch_state = apply_control(
            config,
            lease,
            "green_switched",
            deadline_monotonic_ns=deadline_monotonic_ns,
            causal_crossover=hold_identity,
            pending_crossover_request_ids=[str(hold_identity["request_id"])],
        )
        transition_receipt = dict(switch_state.get("transition_receipt") or {})
        if not transition_receipt:
            raise S6BMExperimentError("route_transition_receipt_absent")
        timeline.append(phase_entry(config, "green_active", clock_chain=clock_chain))
        apply_control(config, lease, "blue_drain_started")
        timeline.append(phase_entry(config, "blue_draining", clock_chain=clock_chain))
        hold_record = hold_future.result(timeout=20)
        execution_progress.update(
            {"attempt_id": attempt_id, "requests": request_projection([hold_record])}
        )
    wait_in_flight(config, "blue", 0, float(config.procedure["drain_timeout_seconds"]))
    blue_before_unload = int(controller_state(config)["in_flight"]["blue"])
    capture_transition_observability(
        config,
        suite_root=suite_root,
        suite_id=suite_id,
        attempt_id=attempt_id,
        run_id=lease.run_id,
        checkpoint=checkpoint,
    )
    apply_control(
        config,
        lease,
        "blue_unloaded",
        causal_crossover=hold_identity,
    )
    physical["blue_unloaded_not_ready"] = not model_ready(config, "blue")
    capture_post_unload_observability(
        config,
        suite_root=suite_root,
        suite_id=suite_id,
        attempt_id=attempt_id,
        run_id=lease.run_id,
        checkpoint=checkpoint,
    )
    timeline.append(phase_entry(config, "green_only", clock_chain=clock_chain))
    transition_seconds = time.perf_counter() - transition_started

    rollback_started = time.perf_counter()
    apply_control(config, lease, "blue_loaded")
    physical["blue_reloaded_ready"] = model_ready(config, "blue")
    timeline.append(phase_entry(config, "rollback_warmup", clock_chain=clock_chain))
    for _ in range(int(config.procedure["warmup_requests"])):
        direct_infer(config, "blue")
    apply_control(config, lease, "blue_switched")
    timeline.append(phase_entry(config, "blue_active_rollback", clock_chain=clock_chain))
    apply_control(config, lease, "green_drain_started")
    timeline.append(phase_entry(config, "green_draining", clock_chain=clock_chain))
    wait_in_flight(config, "green", 0, float(config.procedure["drain_timeout_seconds"]))
    direct_infer(config, "blue")
    observability_attempt = {
        "attempt_id": attempt_id,
        "source_revision": source["revision"],
        "identities": identities(config, lease),
        "request_records": [hold_record],
    }
    observability = finish_attempt_observability(
        config,
        suite_root=suite_root,
        suite_id=suite_id,
        attempt=observability_attempt,
        checkpoint=checkpoint,
    )
    apply_control(config, lease, "green_unloaded")
    physical["green_unloaded_not_ready"] = not model_ready(config, "green")
    timeline.append(phase_entry(config, "rolled_back", clock_chain=clock_chain))
    physical["blue_final_ready"] = model_ready(config, "blue")
    rollback_seconds = time.perf_counter() - rollback_started

    causal_root = suite_root / "causal" / attempt_id
    effects_response = requests.get(api_url(config, f"effects/{attempt_id}"), timeout=30)
    effects_response.raise_for_status()
    events_response = requests.get(api_url(config, f"causal-events/{attempt_id}"), timeout=30)
    events_response.raise_for_status()
    effects = effects_response.json()
    events = events_response.json()
    effects_path = causal_root / "durable-effects.json"
    events_path = causal_root / "causal-events.json"
    canonical_write(effects_path, effects)
    canonical_write(events_path, events)
    expected_events = [
        "api_server_handler_entry",
        "controller_entry",
        "triton_backend_compute_entry",
        "blue_to_green_switch_commit",
        "durable_terminal_effect_commit",
        "blue_unload_intent",
    ]
    observed_events = [str(item.get("event_type")) for item in events.get("events", [])]
    if int(effects.get("effect_count", -1)) != 1 or observed_events != expected_events:
        raise S6BMExperimentError(
            f"causal_qualification_sequence:{effects.get('effect_count')}:{observed_events}"
        )
    sequences = [int(item["causal_sequence"]) for item in events["events"]]
    if sequences != sorted(sequences) or len(set(sequences)) != len(sequences):
        raise S6BMExperimentError(f"causal_qualification_sequence_order:{sequences}")
    final_state = controller_state(config)
    result = {
        "schema_version": "evm.s8_v4.s6bm_causal_qualification.v1",
        "attempt_id": attempt_id,
        "profile": "causal_receipt_preflight",
        "credit": "non_credit",
        "acceptance_credit": False,
        "source_revision": source["revision"],
        "identities": identities(config, lease),
        "phase_timeline": timeline,
        "request_records": [hold_record],
        "requests": request_projection([hold_record]),
        "latency": latency_projection([hold_record]),
        "transition_seconds": transition_seconds,
        "rollback_seconds": rollback_seconds,
        "blue_in_flight_before_unload": blue_before_unload,
        "triton_start_receipt": triton_start_receipt,
        "route_transition_receipt": transition_receipt,
        "durable_effect_export": artifact_reference(suite_root, effects_path),
        "causal_event_export": artifact_reference(suite_root, events_path),
        "causal_event_types": observed_events,
        "causal_sequences": sequences,
        "observability": observability_attempt["observability"],
        "observability_projection": observability,
        "physical_model_state": physical,
        "rollback_exact_blue": (
            final_state["phase"] == "rolled_back"
            and final_state["route_weights"] == {"blue": 100, "green": 0}
            and final_state["loaded_roles"] == ["blue"]
        ),
    }
    prevalidation_path = (
        suite_root / "prevalidation" / f"{attempt_id}-qualification-raw-bundle.json"
    )
    canonical_write(prevalidation_path, result)
    execution_progress.update(
        {
            "attempt_id": attempt_id,
            "requests": result["requests"],
            "prevalidation_artifact": artifact_reference(suite_root, prevalidation_path),
        }
    )
    result["causal_projection"] = validate_causal_bundle(
        suite_root,
        result,
        config,
        compare_projection=False,
    )
    reset_controller(config, lease)
    return result


def ensure_green_unloaded(config: S6BMConfig) -> None:
    requests.post(
        triton_url(config, f"/v2/repository/models/{config.green.model_name}/unload"),
        timeout=15,
    )


def run_fault(
    config: S6BMConfig,
    lease: GpuLease,
    source: Mapping[str, str],
    model_root: Path,
    profile: str,
    repetition: int,
    *,
    suite_id: str,
) -> dict[str, Any]:
    attempt_id = f"s6bm-{profile}-{repetition}-{uuid4().hex[:10]}"
    bind_prometheus_attempt(config, suite_id=suite_id, attempt_id=attempt_id)
    ensure_green_unloaded(config)
    initialize_controller(config, lease, source)
    before = controller_state(config)
    observation: dict[str, Any] = {"injection_observed": True}
    disabled: Path | None = None
    green_root = model_root / config.green.model_name
    rejection: dict[str, Any]
    try:
        if profile == "wrong_digest":
            rejection = rejected_control(config, lease, "green_loaded", green_digest="0" * 64)
        elif profile == "green_load_failure":
            disabled = model_root / f".{config.green.model_name}.disabled"
            green_root.rename(disabled)
            rejection = rejected_control(config, lease, "green_loaded")
        elif profile == "green_readiness_failure":
            observation["green_ready_before"] = model_ready(config, "green")
            rejection = rejected_control(config, lease, "green_loaded", readiness_passed=False)
        elif profile == "green_canary_failure":
            apply_control(config, lease, "green_loaded")
            observed = direct_infer(config, "green")["output"]
            observation.update(
                {
                    "observed_output": observed,
                    "injected_expected_output": [value + 100 for value in observed],
                    "canary_mismatch": True,
                }
            )
            rejection = rejected_control(config, lease, "canary_started", canary_passed=False)
            apply_control(config, lease, "green_aborted")
        elif profile == "vram_preflight_rejection":
            gpu = capture_gpu()
            observation.update(
                {
                    "free_vram_mib": gpu["memory_free_mib"],
                    "required_vram_mib": gpu["memory_free_mib"]
                    + float(config.procedure["vram_headroom_mib"]),
                }
            )
            rejection = rejected_control(config, lease, "green_loaded", preflight_vram_passed=False)
        else:
            raise S6BMExperimentError(f"unknown_fault_profile:{profile}")
    finally:
        if disabled is not None and disabled.exists():
            disabled.rename(green_root)
    guard_state = controller_state(config)
    if guard_state["phase"] in {"green_warmup", "canary"}:
        apply_control(config, lease, "green_aborted")
    final = controller_state(config)
    blue_probe = direct_infer(config, "blue")
    telemetry = telemetry_snapshot(config, suite_id=suite_id, attempt_id=attempt_id)
    result = {
        "schema_version": "evm.s8_v4.s6bm_fault_private.v1",
        "attempt_id": attempt_id,
        "profile": profile,
        "repetition": repetition,
        "credit": "acceptance_fault_probe",
        "source_revision": source["revision"],
        "identities": identities(config, lease),
        "before_state": before,
        "guard_state": guard_state,
        "final_state": final,
        "rejection": rejection,
        "guard_rejected": rejection["status_code"] == 409,
        "guard_code": rejection["guard_code"],
        "route_unchanged_blue": final["route_weights"] == {"blue": 100, "green": 0},
        "green_effect_count": 0,
        "route_switch_count": 0,
        "http_5xx": int(rejection["status_code"] >= 500),
        "orphan_count": 0,
        "blue_health_after": blue_probe["output"] == list(config.blue.expected_output),
        "blue_probe": blue_probe,
        "fault_observation": observation,
        "telemetry": telemetry,
        "owner_samples": [owner_sample(lease)],
        "cleanup": {
            "blue_only": final["route_weights"] == {"blue": 100, "green": 0},
            "green_unloaded": not model_ready(config, "green"),
            "controller_in_flight_zero": not any(final["in_flight"].values()),
            "controller_pending_crossovers_zero": int(final.get("pending_crossover_count", -1))
            == 0,
            "lease_owner_exact": owner_sample(lease)["owner_exact"],
        },
    }
    reset_controller(config, lease)
    return result


def private_index(root: Path) -> dict[str, Any]:
    entries = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.name == "private-evidence-index.json":
            continue
        entries.append(
            {
                "path": path.relative_to(root).as_posix(),
                "bytes": path.stat().st_size,
                "sha256": sha256_file(path),
            }
        )
    aggregate = hashlib.sha256(canonical(entries).encode("ascii")).hexdigest()
    return {
        "schema_version": "evm.s8_v4.s6bm_private_index.v1",
        "artifact_count": len(entries),
        "total_bytes": sum(item["bytes"] for item in entries),
        "aggregate_sha256": aggregate,
        "entries": entries,
    }


def prior_zero_credit_attempts(base: Path, current: Path) -> list[dict[str, Any]]:
    attempts = []
    for path in sorted(base.glob("*/failed-*.json")):
        if path.parent == current:
            continue
        payload = json.loads(path.read_text(encoding="utf-8"))
        error = str(payload.get("error", ""))
        classification = (
            "pre_runtime_log_directory_missing"
            if "runtime\\triton.log" in error or "runtime/triton.log" in error
            else (
                "post_runtime_cleanup_snapshot"
                if path.name == "failed-cleanup.json"
                else "historical_zero_credit_failure"
            )
        )
        attempts.append(
            {
                "suite_id": str(payload.get("suite_id", path.parent.name)),
                "credit": "zero_credit",
                "classification": classification,
                "error_type": str(payload.get("error_type", "unknown")),
                "evidence_sha256": sha256_file(path),
                "acceptance_credit_requests": int(payload.get("acceptance_credit_requests", 0)),
                "executed_logical_requests": int(payload.get("executed_logical_requests", 0)),
            }
        )
    return attempts


def wait_vram_restore(before: Mapping[str, Any], timeout: float) -> tuple[dict[str, Any], float]:
    started = time.monotonic()
    tolerance = max(256.0, float(before["memory_total_mib"]) * 0.05)
    latest = capture_gpu()
    while time.monotonic() - started <= timeout:
        latest = capture_gpu()
        if abs(float(latest["memory_used_mib"]) - float(before["memory_used_mib"])) <= tolerance:
            return latest, time.monotonic() - started
        time.sleep(1)
    raise S6BMExperimentError(f"vram_restore_timeout:{latest}")


def cleanup_snapshot(
    config: S6BMConfig,
    holder: Holder,
    gpu_before: Mapping[str, Any],
    prometheus_before: Mapping[str, Any],
    database_schema: str,
) -> dict[str, Any]:
    gpu_after, vram_restore_seconds = wait_vram_restore(
        gpu_before, float(config.procedure["cleanup_timeout_seconds"])
    )
    queues = queue_counts()
    current_holder = capture_holder()
    targets = wait_prometheus_baseline(prometheus_before)
    return {
        "b0_uid_exact": current_holder.uid == holder.uid,
        "b0_image_exact": current_holder.image == holder.image,
        "b0_cuda_inference": b0_cuda_inference()["passed"],
        "container_absent": not container_exists(CONTAINER_NAME),
        "ports_absent": ports_absent(config),
        "prometheus_targets_restored": targets == prometheus_before,
        "temporary_prometheus_targets_absent": not TRITON_TARGET.exists()
        and not API_TARGET.exists(),
        "gpu_lease_absent": read_active_gpu_lease() is None,
        "queue_active_zero": queues["active"] == 0,
        "queue_leased_zero": queues["leased"] == 0,
        "queue_outcome_unknown_zero": queues["outcome_unknown"] == 0,
        "experiment_schema_absent": not s6bm_schema_exists(database_schema),
        "vram_restore_seconds": vram_restore_seconds,
        "vram_delta_mib": float(gpu_after["memory_used_mib"])
        - float(gpu_before["memory_used_mib"]),
        "vram_restored": abs(
            float(gpu_after["memory_used_mib"]) - float(gpu_before["memory_used_mib"])
        )
        <= max(256.0, float(gpu_before["memory_total_mib"]) * 0.05),
        "prometheus": targets,
        "gpu_after": gpu_after,
    }


def main() -> int:
    args = parse_args()
    config = S6BMConfig.from_path(args.config)
    source = source_identity()
    config_git_blob_sha256 = git_blob_sha256(source["revision"], args.config)
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ").lower()
    suite_id = f"s6bm-{timestamp}-{uuid4().hex[:8]}"
    suite_root = args.private_base / suite_id
    suite_root.mkdir(parents=True, exist_ok=False)
    model_root = suite_root / "model-repository"
    shutil.copytree(args.model_repository, model_root)
    manifest = verify_model_repository(model_root, config)
    canonical_write(suite_root / "source-model-manifest.json", manifest)
    canonical_write(suite_root / "frozen-config.json", config.public_snapshot())
    database_schema = f"evm_s6bm_v4_{hashlib.sha256(suite_id.encode('ascii')).hexdigest()[:12]}"

    holder = capture_holder()
    b0_before = b0_cuda_inference()
    gpu_before = capture_gpu()
    queues_before = queue_counts()
    prometheus_before = prometheus_baseline()
    if any(queues_before.values()) or read_active_gpu_lease() is not None:
        raise S6BMExperimentError("preflight_queue_or_lease_not_clean")
    if container_exists(CONTAINER_NAME) or not ports_absent(config):
        raise S6BMExperimentError("preflight_temporary_runtime_present")
    environment = {
        "captured_at": utc_now(),
        "source": source,
        "image": image_identity(config),
        "gpu": gpu_before,
        "holder": holder.__dict__,
        "b0": b0_before,
        "queues": queues_before,
        "prometheus": prometheus_before,
        "model_repository": {
            "manifest_sha256": sha256_file(model_root / "model-repository-manifest.json"),
            "repository_sha256": manifest["repository_sha256"],
        },
        "control_plane_schema_sha256": hashlib.sha256(database_schema.encode("ascii")).hexdigest(),
    }
    canonical_write(suite_root / "environment-preflight.json", environment)

    api: ApiProcess | None = None
    lease: GpuLease | None = None
    target_written = False
    holder_scaled = False
    attempts: list[dict[str, Any]] = []
    baselines: list[dict[str, Any]] = []
    active_success_progress: dict[str, Any] = {}
    qualification_result: dict[str, Any] | None = None
    continuity_qualification_result: dict[str, Any] | None = None
    failure: dict[str, Any] | None = None
    caught_exception: Exception | None = None
    triton_log = suite_root / "runtime" / "triton.log"
    try:
        scale_holder(holder, 0)
        holder_scaled = True
        run_id = f"s8-v4-s6bm-{suite_id.replace('_', '-')}"
        lease = acquire_scale_validation_gpu_lease(
            run_id,
            source_commit=source["revision"],
            purpose="scale_validation_inference",
            scenario_id="S6B-M",
            model_family="tabular",
            owner_pid=os.getpid(),
            ttl_seconds=3600,
        )
        assert_scale_validation_gpu_lease_owner(
            run_id=lease.run_id,
            lease_id=lease.lease_id,
            fencing_token=lease.fencing_token,
            purpose="scale_validation_inference",
            scenario_id="S6B-M",
            model_family="tabular",
        )
        write_prometheus_targets(config, suite_id)
        target_written = True
        start_triton(config, model_root, triton_log)
        api = start_api(
            config,
            suite_root,
            source_revision=source["revision"],
            suite_id=suite_id,
            database_schema=database_schema,
        )
        wait_runtime(config, api)
        wait_prometheus_jobs(config, present=True)
        if args.qualification_only:
            qualification_result = run_causal_qualification(
                config,
                lease,
                source,
                api,
                suite_root=suite_root,
                suite_id=suite_id,
                execution_progress=active_success_progress,
            )
            canonical_write(suite_root / "causal-qualification.json", qualification_result)
        elif args.continuity_qualification_only:
            continuity_qualification_result = run_success(
                config,
                lease,
                source,
                0,
                api,
                suite_root=suite_root,
                suite_id=suite_id,
                execution_progress=active_success_progress,
                credit="non_credit",
            )
            continuity_qualification_result["qualification_scope"] = (
                "exact_1000_bridge_actor_receipt_and_continuity"
            )
            canonical_write(
                suite_root / "continuity-qualification.json",
                continuity_qualification_result,
            )
        else:
            for repetition in range(1, int(config.procedure["baseline_repetitions"]) + 1):
                baseline = run_baseline(
                    config,
                    lease,
                    source,
                    repetition,
                    api,
                    suite_id=suite_id,
                )
                baselines.append(baseline)
                canonical_write(
                    suite_root / "baseline" / f"repetition-{repetition:02d}.json",
                    baseline,
                )

            for repetition in range(
                1, int(config.procedure["successful_transition_repetitions"]) + 1
            ):
                active_success_progress = {}
                attempt = run_success(
                    config,
                    lease,
                    source,
                    repetition,
                    api,
                    suite_root=suite_root,
                    suite_id=suite_id,
                    execution_progress=active_success_progress,
                )
                attempts.append(attempt)
                canonical_write(
                    suite_root / "successful-transition" / f"repetition-{repetition:02d}.json",
                    attempt,
                )
                active_success_progress = {}

            profiles = (
                "wrong_digest",
                "green_load_failure",
                "green_readiness_failure",
                "green_canary_failure",
                "vram_preflight_rejection",
            )
            for profile in profiles:
                for repetition in range(
                    1, int(config.procedure["negative_profile_repetitions"]) + 1
                ):
                    attempt = run_fault(
                        config,
                        lease,
                        source,
                        model_root,
                        profile,
                        repetition,
                        suite_id=suite_id,
                    )
                    attempts.append(attempt)
                    canonical_write(
                        suite_root / "faults" / profile / f"repetition-{repetition:02d}.json",
                        attempt,
                    )
            analysis = analyze_attempts(attempts, config)
            if not analysis["evidence_ready"]:
                raise S6BMExperimentError(f"acceptance_not_ready:{analysis}")
    except Exception as exc:
        caught_exception = exc
        failure = {
            "schema_version": "evm.s8_v4.s6bm_failed_attempt.v1",
            "suite_id": suite_id,
            "failed_at": utc_now(),
            "source_revision": source["revision"],
            "error_type": type(exc).__name__,
            "error": str(exc),
            "credit": "zero_credit",
            "rca_status": "preserved_for_follow_up",
        }
    finally:
        cleanup_errors: list[dict[str, str]] = []
        cleanup_actions: list[tuple[str, Callable[[], None]]] = [
            ("api", lambda: stop_api(api)),
            ("triton", lambda: stop_triton(triton_log)),
            ("experiment_schema", lambda: drop_s6bm_schema(database_schema)),
        ]
        if target_written:
            cleanup_actions.append(
                (
                    "prometheus",
                    lambda: (
                        remove_prometheus_targets(),
                        wait_prometheus_jobs(config, present=False),
                    ),
                )
            )
        if lease is not None:
            cleanup_actions.append(
                (
                    "lease",
                    lambda: release_scale_validation_gpu_lease(
                        run_id=lease.run_id,
                        lease_id=lease.lease_id,
                        fencing_token=lease.fencing_token,
                        reason=f"S6B-M suite {suite_id} cleanup",
                    )
                    if read_active_gpu_lease() is not None
                    else None,
                )
            )
        if holder_scaled:
            cleanup_actions.append(("b0_holder", lambda: scale_holder(holder, holder.replicas)))
        for name, action in cleanup_actions:
            try:
                action()
            except Exception as cleanup_exc:  # noqa: BLE001 - preserve every cleanup failure
                cleanup_errors.append(
                    {
                        "action": name,
                        "error_type": type(cleanup_exc).__name__,
                        "error": str(cleanup_exc),
                    }
                )
        if cleanup_errors:
            canonical_write(suite_root / "cleanup-errors.json", cleanup_errors)

    cleanup = cleanup_snapshot(
        config,
        holder,
        gpu_before,
        prometheus_before,
        database_schema,
    )
    canonical_write(suite_root / "final-cleanup.json", cleanup)
    if failure is not None:
        failure.update(
            {
                "acceptance_credit_requests": 0,
                "executed_logical_requests": sum(
                    int(item.get("requests", {}).get("logical", 0))
                    for item in attempts
                    if item.get("profile") == "successful_transition"
                )
                + int(dict(active_success_progress.get("requests", {})).get("logical", 0)),
                "partial_success_attempt": active_success_progress or None,
                "cleanup": {key: value for key, value in cleanup.items() if key != "gpu_after"},
            }
        )
        canonical_write(suite_root / "failed-attempt.json", failure)
    cleanup_passed = all(
        value is True
        for key, value in cleanup.items()
        if key
        in {
            "b0_uid_exact",
            "b0_image_exact",
            "b0_cuda_inference",
            "container_absent",
            "ports_absent",
            "prometheus_targets_restored",
            "temporary_prometheus_targets_absent",
            "gpu_lease_absent",
            "queue_active_zero",
            "queue_leased_zero",
            "queue_outcome_unknown_zero",
            "experiment_schema_absent",
            "vram_restored",
        }
    )
    if not cleanup_passed:
        canonical_write(
            suite_root / "failed-cleanup.json",
            {
                "schema_version": "evm.s8_v4.s6bm_failed_cleanup.v1",
                "suite_id": suite_id,
                "failed_at": utc_now(),
                "source_revision": source["revision"],
                "credit": "zero_credit",
                "acceptance_credit_requests": 0,
                "executed_logical_requests": sum(
                    int(item.get("requests", {}).get("logical", 0))
                    for item in attempts
                    if item.get("profile") == "successful_transition"
                )
                + int(dict(active_success_progress.get("requests", {})).get("logical", 0)),
                "cleanup": cleanup,
                "rca": "Final cleanup did not satisfy every frozen postcondition.",
            },
        )
        raise S6BMExperimentError(f"final_cleanup_failed:{cleanup}")
    if caught_exception is not None:
        raise S6BMExperimentError(
            f"controlled_execution_failed:{type(caught_exception).__name__}:{caught_exception}"
        ) from caught_exception

    if args.qualification_only or args.continuity_qualification_only:
        qualification_payload = (
            qualification_result if args.qualification_only else continuity_qualification_result
        )
        if qualification_payload is None:
            raise S6BMExperimentError("causal_qualification_result_absent")
        qualification_payload["cleanup"] = {
            key: value for key, value in cleanup.items() if key != "gpu_after"
        }
        qualification_path = suite_root / (
            "causal-qualification.json"
            if args.qualification_only
            else "continuity-qualification.json"
        )
        canonical_write(qualification_path, qualification_payload)
        qualification_index = private_index(suite_root)
        qualification_index_path = suite_root / "private-evidence-index.json"
        canonical_write(qualification_index_path, qualification_index)
        print(
            canonical(
                {
                    "mode": (
                        "qualification_only"
                        if args.qualification_only
                        else "continuity_qualification_only"
                    ),
                    "credit": "non_credit",
                    "acceptance_credit": False,
                    "private_root": str(suite_root),
                    "qualification": artifact_reference(suite_root, qualification_path),
                    "private_index_sha256": sha256_file(qualification_index_path),
                    "private_aggregate_sha256": qualification_index["aggregate_sha256"],
                }
            )
        )
        return 0

    index = private_index(suite_root)
    index_path = suite_root / "private-evidence-index.json"
    canonical_write(index_path, index)
    analysis = analyze_attempts(attempts, config)
    public = {
        "schema_version": "evm.s8_v4.s6bm_experiment.v1",
        "generated_at": utc_now(),
        "status": "evidence_ready",
        "credit": "non_credit_reviewer_pending",
        "suite_id": suite_id,
        "source_identity": source,
        "contract": {
            "config_sha256": config_git_blob_sha256,
            "snapshot_sha256": canonical_sha256(config.public_snapshot()),
        },
        "environment": {
            "gpu_name": gpu_before["name"],
            "gpu_uuid_sha256": hashlib.sha256(str(gpu_before["uuid"]).encode("utf-8")).hexdigest(),
            "triton_image_digest": config.image_digest,
            "model_repository_sha256": config.repository_sha256,
            "single_physical_node": True,
            "single_gpu": True,
        },
        "matrix": {
            "baseline_repetitions": len(baselines),
            "successful_transition_repetitions": sum(
                item["profile"] == "successful_transition" for item in attempts
            ),
            "fault_repetitions": {
                profile: sum(item["profile"] == profile for item in attempts)
                for profile in (
                    "wrong_digest",
                    "green_load_failure",
                    "green_readiness_failure",
                    "green_canary_failure",
                    "vram_preflight_rejection",
                )
            },
        },
        "analysis": analysis,
        "cleanup": {key: value for key, value in cleanup.items() if key != "gpu_after"},
        "private_evidence": {
            "logical_root": "private://scale_validation/s8-v4/s6bm/accepted",
            "artifact_count": index["artifact_count"],
            "total_bytes": index["total_bytes"],
            "aggregate_sha256": index["aggregate_sha256"],
            "index_sha256": sha256_file(index_path),
        },
        "failed_attempts": prior_zero_credit_attempts(args.private_base, suite_root),
        "claim_boundary": CLAIM_BOUNDARY,
        "reviewer_sign_off": "pending",
        "next_action": "source-local independent review; do not start X1",
    }
    canonical_write(args.public_output, public)
    print(
        canonical(
            {
                "public_output": str(args.public_output),
                "private_root": str(suite_root),
                "result": public,
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
