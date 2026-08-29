from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import math
import os
import socket
import subprocess
import sys
import threading
import time
from collections import Counter
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence
from uuid import uuid4

import requests


ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from evm.control_panel.scenario_workloads import (  # noqa: E402
    GpuLease,
    acquire_scale_validation_gpu_lease,
    assert_scale_validation_gpu_lease_owner,
    read_active_gpu_lease,
    release_scale_validation_gpu_lease,
)
from evm.scale_validation.x1_artifacts import (  # noqa: E402
    PROFILE_IDS,
    prepare_x1_artifacts,
    validate_x1_artifacts,
)
from evm.scale_validation.x1_calibration import (  # noqa: E402
    project_calibration_attempt,
)
from evm.scale_validation.x1_contract import (  # noqa: E402
    API_REPLICAS,
    CPU_WORKERS,
    GPU_NAME,
    GPU_UUID,
    KERNEL_OVERLAP_FALLBACK,
    MODEL_IDS,
    REPETITIONS,
    X1Contract,
    canonical_sha256,
    compute_load_freeze,
    sha256_file,
)
from evm.scale_validation.x1_runtime import (  # noqa: E402
    X1RuntimeValidationError,
    build_runtime_manifest,
    normalize_triton_repository_index,
    select_batching_profiles,
    validate_triton_runtime_config,
    validate_q0_bundle,
    validate_runtime_profile_files,
)
from evm.scale_validation.x1_load_freeze_validation import (  # noqa: E402
    source_blob_inventory,
)
from evm.scale_validation.x1_topology import (  # noqa: E402
    API_NAME,
    NAMESPACE,
    TRITON_NAME,
    kubernetes_resource_list,
    validate_runtime_topology_readback,
)
from scripts.dev.run_s8_v4_s6bm_experiment import (  # noqa: E402
    Holder,
    b0_cuda_inference,
    capture_gpu,
    capture_holder,
    prometheus_baseline,
    prometheus_targets,
    queue_counts,
    scale_holder,
    source_identity,
    wait_prometheus_baseline,
)


DATA_ROOT = Path("F:/EnterpriseMLOps_Data/enterprise-vision-mlops")
PRIVATE_BASE = DATA_ROOT / "artifacts/scale_validation/private/s8-v4/x1"
PUBLIC_OUTPUT = ROOT / "docs/status/evidence/s8-v4-x1-load-freeze-v1.json"
TARGET_ROOT = DATA_ROOT / "artifacts/w7/prometheus-targets"
TRITON_TARGET = TARGET_ROOT / "s8-v4-x1-triton.json"
API_TARGET = TARGET_ROOT / "s8-v4-x1-api.json"
PROMETHEUS_URL = "http://127.0.0.1:9090"
API_URL = "http://127.0.0.1:31120"
TRITON_URL = "http://127.0.0.1:31121"
TRITON_METRICS_URL = "http://127.0.0.1:31122/metrics"
Q0_DIAGNOSTIC_HTTP_PORT = 32121
Q0_DIAGNOSTIC_METRICS_PORT = 32122
OTEL_TRACE_FILE = DATA_ROOT / "artifacts/scale_validation/otel/traces.json"
CONTROL_PLANE_DATABASE_URL = (
    "postgresql://evm_control_plane:evm_control_plane_local@"
    "host.docker.internal:5434/evm_control_plane"
)
CONTROL_PLANE_HOST_DATABASE_URL = (
    "postgresql://evm_control_plane:evm_control_plane_local@127.0.0.1:5434/evm_control_plane"
)
EXPECTED_BASELINE_PROMETHEUS_JOBS = {
    "evm-api",
    "evm-task-queue-worker",
    "evm-b0-production",
    "evm-otel-collector",
    "prometheus",
}


class X1ExperimentError(RuntimeError):
    pass


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run canonical S8-V4 X1 artifact Q0 and non-credit load calibration."
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=ROOT / "configs/s8_v4_x1_heterogeneous_v1.toml",
    )
    parser.add_argument("--data-root", type=Path, default=DATA_ROOT)
    parser.add_argument("--private-base", type=Path, default=PRIVATE_BASE)
    parser.add_argument("--public-output", type=Path, default=PUBLIC_OUTPUT)
    parser.add_argument("--maintenance-approved", action="store_true")
    return parser.parse_args()


def utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def run(
    command: Sequence[str],
    *,
    timeout: float = 60,
    check: bool = True,
    input_text: str | None = None,
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        list(command),
        cwd=ROOT,
        input=input_text,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=False,
    )
    if check and result.returncode != 0:
        raise X1ExperimentError(
            f"command_failed:{result.returncode}:{' '.join(command)}:{result.stderr[-2000:]}"
        )
    return result


def run_json(command: Sequence[str], *, timeout: float = 60) -> Any:
    try:
        return json.loads(run(command, timeout=timeout).stdout)
    except json.JSONDecodeError as exc:
        raise X1ExperimentError(f"command_json:{' '.join(command)}") from exc


def canonical_bytes(value: Any) -> bytes:
    return (
        json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("ascii")


def canonical_write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = canonical_bytes(value)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
    descriptor = os.open(path, flags)
    try:
        os.write(descriptor, payload)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def source_snapshot() -> dict[str, str]:
    value = source_identity()
    local = run(["git", "rev-parse", "HEAD"], timeout=30).stdout.strip()
    origin = run(
        ["git", "rev-parse", "origin/codex/distributed-scale-validation-plan"], timeout=30
    ).stdout.strip()
    remote = run(
        ["git", "ls-remote", "origin", "refs/heads/codex/distributed-scale-validation-plan"],
        timeout=60,
    ).stdout.split()[0]
    if local != origin or local != remote or value["revision"] != local:
        raise X1ExperimentError(f"source_remote_mismatch:{local}:{origin}:{remote}")
    if value["branch"] != "codex/distributed-scale-validation-plan":
        raise X1ExperimentError(f"source_branch:{value['branch']}")
    return {**value, "origin_revision": origin, "remote_revision": remote}


def assert_prometheus_baseline(snapshot: Mapping[str, Any]) -> None:
    if (
        snapshot.get("total") != 5
        or snapshot.get("up") != 5
        or set(snapshot.get("jobs", [])) != EXPECTED_BASELINE_PROMETHEUS_JOBS
    ):
        raise X1ExperimentError(f"prometheus_baseline:{snapshot}")


def resource_exists(kind: str, name: str) -> bool:
    return (
        run(
            ["kubectl", "-n", NAMESPACE, "get", f"{kind}/{name}", "-o", "name"],
            check=False,
            timeout=15,
        ).returncode
        == 0
    )


def port_open(port: int) -> bool:
    with socket.socket() as probe:
        probe.settimeout(0.2)
        return probe.connect_ex(("127.0.0.1", port)) == 0


def x1_runtime_absent() -> bool:
    resources = any(
        resource_exists(kind, name)
        for kind, name in (
            ("deployment", API_NAME),
            ("deployment", TRITON_NAME),
            ("service", API_NAME),
            ("service", TRITON_NAME),
        )
    )
    return (
        not resources
        and not any(port_open(port) for port in (31120, 31121, 31122))
        and not TRITON_TARGET.exists()
        and not API_TARGET.exists()
    )


def preflight(contract: X1Contract) -> dict[str, Any]:
    source = source_snapshot()
    holder = capture_holder()
    b0 = b0_cuda_inference()
    gpu = capture_gpu()
    queues = queue_counts()
    prometheus = prometheus_baseline()
    assert_prometheus_baseline(prometheus)
    if gpu["uuid"] != GPU_UUID or gpu["name"] != GPU_NAME:
        raise X1ExperimentError("x1_gpu_identity")
    if any(queues.values()) or read_active_gpu_lease() is not None:
        raise X1ExperimentError(f"x1_queue_or_lease_preflight:{queues}")
    if not x1_runtime_absent():
        raise X1ExperimentError("x1_runtime_preflight_residue")
    node = run_json(["kubectl", "get", "node", "-o", "json"])
    items = node.get("items") if isinstance(node, Mapping) else None
    if not isinstance(items, list) or len(items) != 1:
        raise X1ExperimentError("x1_kubernetes_single_node")
    status = items[0].get("status", {})
    capacity = status.get("capacity", {})
    allocatable = status.get("allocatable", {})
    if capacity.get("nvidia.com/gpu") != "1" or allocatable.get("nvidia.com/gpu") != "1":
        raise X1ExperimentError("x1_kubernetes_gpu_capacity")
    return {
        "schema_version": "evm.s8_v4.x1_environment_preflight.v1",
        "captured_at": utc_now(),
        "source": source,
        "contract_sha256": contract.sha256,
        "holder": asdict(holder),
        "b0": b0,
        "gpu": gpu,
        "queues": queues,
        "prometheus": prometheus,
        "kubernetes": {
            "node_uid": items[0]["metadata"]["uid"],
            "gpu_capacity": capacity["nvidia.com/gpu"],
            "gpu_allocatable": allocatable["nvidia.com/gpu"],
        },
        "x1_runtime_absent": True,
    }


def build_api_image(source_revision: str) -> str:
    tag = f"evm-x1-api:{source_revision[:12]}"
    run(
        [
            "docker",
            "build",
            "-t",
            tag,
            "--build-arg",
            f"SOURCE_REVISION={source_revision}",
            "-f",
            "apps/api/Dockerfile",
            ".",
        ],
        timeout=1200,
    )
    payload = run_json(["docker", "image", "inspect", tag])
    if not isinstance(payload, list) or len(payload) != 1:
        raise X1ExperimentError("x1_api_image_inspect")
    labels = payload[0].get("Config", {}).get("Labels", {})
    if labels.get("org.opencontainers.image.revision") != source_revision:
        raise X1ExperimentError("x1_api_image_revision")
    return tag


def _target_payload(target: str, suite_id: str) -> list[dict[str, Any]]:
    return [
        {
            "targets": [target],
            "labels": {"scenario": "s8-v4-x1", "suite_id": suite_id},
        }
    ]


def _prometheus_reload() -> None:
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
        raise X1ExperimentError("x1_prometheus_config")
    run(["docker", "kill", "--signal", "SIGHUP", "evm-prometheus"], timeout=30)


def write_prometheus_targets(suite_id: str) -> None:
    if TRITON_TARGET.exists() or API_TARGET.exists():
        raise X1ExperimentError("x1_prometheus_target_exists")
    canonical_write(TRITON_TARGET, _target_payload("host.docker.internal:31122", suite_id))
    canonical_write(API_TARGET, _target_payload("host.docker.internal:31120", suite_id))
    _prometheus_reload()


def wait_x1_prometheus(*, present: bool, timeout: float = 60) -> dict[str, Any]:
    expected = {"evm-s8-v4-x1-api", "evm-s8-v4-x1-triton"}
    deadline = time.monotonic() + timeout
    latest: dict[str, Any] = {}
    while time.monotonic() < deadline:
        targets = prometheus_targets()
        observed = {
            str(item.get("labels", {}).get("job")) for item in targets if isinstance(item, Mapping)
        }
        healthy = {
            str(item.get("labels", {}).get("job"))
            for item in targets
            if isinstance(item, Mapping) and item.get("health") == "up"
        }
        latest = {"observed": sorted(observed), "healthy": sorted(healthy)}
        if (present and expected <= healthy) or (not present and not (expected & observed)):
            return latest
        time.sleep(1)
    raise X1ExperimentError(f"x1_prometheus_target_timeout:{present}:{latest}")


def remove_prometheus_targets() -> None:
    empty_target_group = [{"targets": []}]
    for path in (TRITON_TARGET, API_TARGET):
        temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
        canonical_write(temporary, empty_target_group)
        os.replace(temporary, path)
    _prometheus_reload()
    wait_x1_prometheus(present=False)
    for path in (TRITON_TARGET, API_TARGET):
        path.unlink(missing_ok=True)
    _prometheus_reload()
    wait_x1_prometheus(present=False)


def apply_topology(
    contract: X1Contract,
    *,
    suite_root: Path,
    suite_id: str,
    source_revision: str,
    api_image: str,
    api_replicas: int,
    cpu_workers: int,
    profile_relative_root: str,
    runtime_manifest_relative_path: str,
    database_schema: str,
    lease: GpuLease,
) -> dict[str, Any]:
    bundle = kubernetes_resource_list(
        contract,
        suite_id=suite_id,
        source_revision=source_revision,
        api_image=api_image,
        api_replicas=api_replicas,
        cpu_workers=cpu_workers,
        profile_relative_root=profile_relative_root,
        runtime_manifest_relative_path=runtime_manifest_relative_path,
        database_url=CONTROL_PLANE_DATABASE_URL,
        database_schema=database_schema,
        lease_id=lease.lease_id,
        fencing_token=lease.fencing_token,
    )
    manifest_path = (
        suite_root
        / "runtime"
        / f"kubernetes-r{api_replicas}-w{cpu_workers}-{Path(profile_relative_root).name}.json"
    )
    if not manifest_path.exists():
        canonical_write(manifest_path, bundle)
    run(["kubectl", "apply", "-f", str(manifest_path)], timeout=120)
    for name in (TRITON_NAME, API_NAME):
        run(
            [
                "kubectl",
                "-n",
                NAMESPACE,
                "rollout",
                "status",
                f"deployment/{name}",
                "--timeout=180s",
            ],
            timeout=200,
        )
    return capture_topology_readback(
        expected_replicas=api_replicas,
        expected_workers=cpu_workers,
    )


def capture_topology_readback(*, expected_replicas: int, expected_workers: int) -> dict[str, Any]:
    triton = run_json(
        ["kubectl", "-n", NAMESPACE, "get", f"deployment/{TRITON_NAME}", "-o", "json"]
    )
    api = run_json(["kubectl", "-n", NAMESPACE, "get", f"deployment/{API_NAME}", "-o", "json"])
    pods = run_json(
        [
            "kubectl",
            "-n",
            NAMESPACE,
            "get",
            "pods",
            "-l",
            f"app.kubernetes.io/name={API_NAME}",
            "-o",
            "json",
        ]
    )
    api_items = pods.get("items", [])
    pod_uids = sorted(
        str(item["metadata"]["uid"])
        for item in api_items
        if item.get("status", {}).get("phase") == "Running"
        and all(
            condition.get("status") == "True"
            for condition in item.get("status", {}).get("conditions", [])
            if condition.get("type") == "Ready"
        )
    )
    snapshot = {
        "triton_pods_ready": int(triton.get("status", {}).get("readyReplicas", 0)),
        "triton_gpu_limits": int(
            triton["spec"]["template"]["spec"]["containers"][0]["resources"]["limits"].get(
                "nvidia.com/gpu", 0
            )
        ),
        "api_pods_ready": int(api.get("status", {}).get("readyReplicas", 0)),
        "observed_api_pod_uids": pod_uids,
        "observed_worker_slots_by_pod": {pod_uid: [] for pod_uid in pod_uids},
        "client_lanes_are_server_workers": False,
    }
    if snapshot["api_pods_ready"] != expected_replicas:
        raise X1ExperimentError("x1_topology_api_not_ready")
    # Worker attribution is populated from accepted response records before validation.
    if expected_workers not in CPU_WORKERS:
        raise X1ExperimentError("x1_topology_worker_axis")
    return snapshot


def delete_topology() -> None:
    resources = [
        f"deployment/{API_NAME}",
        f"service/{API_NAME}",
        f"deployment/{TRITON_NAME}",
        f"service/{TRITON_NAME}",
    ]
    run(
        ["kubectl", "-n", NAMESPACE, "delete", *resources, "--ignore-not-found=true"],
        timeout=180,
    )
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        if not any(
            resource_exists(kind, name)
            for kind, name in (
                ("deployment", API_NAME),
                ("deployment", TRITON_NAME),
                ("service", API_NAME),
                ("service", TRITON_NAME),
            )
        ) and not any(port_open(port) for port in (31120, 31121, 31122)):
            return
        time.sleep(1)
    raise X1ExperimentError("x1_topology_cleanup_timeout")


def database_schema_exists(schema: str) -> bool:
    import psycopg

    with psycopg.connect(CONTROL_PLANE_HOST_DATABASE_URL) as connection:
        row = connection.execute(
            "SELECT EXISTS(SELECT 1 FROM pg_namespace WHERE nspname=%s)",
            (schema,),
        ).fetchone()
    return bool(row[0])


def drop_database_schema(schema: str) -> None:
    if not schema.startswith("evm_x1_v1_") or not schema.replace("_", "").isalnum():
        raise X1ExperimentError("x1_database_schema_identity")
    import psycopg

    with psycopg.connect(CONTROL_PLANE_HOST_DATABASE_URL, autocommit=True) as connection:
        connection.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
    if database_schema_exists(schema):
        raise X1ExperimentError("x1_database_schema_cleanup")


def artifact_relative(path: Path, data_root: Path) -> str:
    try:
        return path.resolve().relative_to(data_root.resolve()).as_posix()
    except ValueError as exc:
        raise X1ExperimentError(f"x1_data_root_containment:{path}") from exc


def write_runtime_manifest(
    contract: X1Contract,
    *,
    artifact_root: Path,
    artifact_manifest_path: Path,
    artifact_manifest: Mapping[str, Any],
    data_root: Path,
    profile_id: str,
    lease: GpuLease,
    source_revision: str,
) -> Path:
    validate_runtime_profile_files(
        artifact_root=artifact_root,
        artifact_manifest=artifact_manifest,
        profile_id=profile_id,
    )
    path = artifact_root / f"x1-runtime-manifest-{profile_id}.json"
    if not path.exists():
        payload = build_runtime_manifest(
            contract,
            artifact_manifest=artifact_manifest,
            artifact_manifest_file_sha256=sha256_file(artifact_manifest_path),
            artifact_manifest_container_path=(
                f"/mnt/evm-data/{artifact_relative(artifact_manifest_path, data_root)}"
            ),
            active_profile=profile_id,
            lease_run_id=lease.run_id,
            lease_id=lease.lease_id,
            fencing_token_sha256=hashlib.sha256(lease.fencing_token.encode("utf-8")).hexdigest(),
            source_revision=source_revision,
        )
        canonical_write(path, payload)
    return path


def wait_runtime_ready(timeout: float = 180) -> None:
    deadline = time.monotonic() + timeout
    latest = ""
    while time.monotonic() < deadline:
        try:
            api = requests.get(f"{API_URL}/control-panel/v1/scenario-workloads/x1/ready", timeout=2)
            triton_live = requests.get(f"{TRITON_URL}/v2/health/live", timeout=2)
            triton_ready = requests.get(f"{TRITON_URL}/v2/health/ready", timeout=2)
            model_states = [
                requests.get(
                    f"{TRITON_URL}/v2/models/{model_id}/versions/1/ready", timeout=2
                ).status_code
                for model_id in MODEL_IDS
            ]
            latest = (
                f"api={api.status_code}:live={triton_live.status_code}:"
                f"ready={triton_ready.status_code}:models={model_states}"
            )
            if (
                api.status_code == 200
                and triton_live.status_code == 200
                and triton_ready.status_code == 200
                and model_states == [200, 200, 200, 200]
            ):
                return
        except requests.RequestException as exc:
            latest = type(exc).__name__
        time.sleep(1)
    logs = run(
        ["kubectl", "-n", NAMESPACE, "logs", f"deployment/{TRITON_NAME}", "--tail=200"],
        check=False,
        timeout=30,
    )
    raise X1ExperimentError(f"x1_runtime_readiness:{latest}:{logs.stdout[-1500:]}")


def repository_index() -> list[dict[str, str]]:
    response = requests.post(f"{TRITON_URL}/v2/repository/index", json={}, timeout=10)
    response.raise_for_status()
    try:
        return normalize_triton_repository_index(response.json())
    except X1RuntimeValidationError as exc:
        raise X1ExperimentError(str(exc)) from exc


def triton_config_readback(model_id: str) -> dict[str, Any]:
    response = requests.get(f"{TRITON_URL}/v2/models/{model_id}/versions/1/config", timeout=10)
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise X1ExperimentError("x1_triton_config_schema")
    return payload


def triton_metrics_text() -> str:
    response = requests.get(TRITON_METRICS_URL, timeout=10)
    response.raise_for_status()
    if not response.text.endswith("\n"):
        raise X1ExperimentError("x1_triton_metrics_terminal_lf")
    return response.text


def _metric_values(text: str, model_id: str) -> dict[str, float]:
    from prometheus_client.parser import text_string_to_metric_families

    names = {
        "success_count": "nv_inference_request_success",
        "inference_count": "nv_inference_count",
        "execution_count": "nv_inference_exec_count",
        "compute_duration_us": "nv_inference_compute_infer_duration_us",
    }
    samples: list[tuple[str, Mapping[str, str], float]] = []
    for family in text_string_to_metric_families(text):
        for sample in family.samples:
            samples.append((sample.name, dict(sample.labels), float(sample.value)))
    result: dict[str, float] = {}
    for field, name in names.items():
        accepted = {name, f"{name}_total"}
        matches = [
            value
            for sample_name, labels, value in samples
            if sample_name in accepted
            and labels.get("model") == model_id
            and labels.get("version") == "1"
        ]
        if len(matches) != 1 or not math.isfinite(matches[0]) or matches[0] < 0:
            raise X1ExperimentError(f"x1_metric_identity:{model_id}:{field}:{len(matches)}")
        result[field] = matches[0]
    return result


def _metric_delta(before: str, after: str, model_id: str) -> dict[str, int | float]:
    prior = _metric_values(before, model_id)
    observed = _metric_values(after, model_id)
    result: dict[str, int | float] = {}
    for field in prior:
        delta = observed[field] - prior[field]
        if delta < 0 or not math.isfinite(delta):
            raise X1ExperimentError(f"x1_metric_counter_decrease:{model_id}:{field}")
        result[field] = int(delta) if field != "compute_duration_us" else delta
    return result


def _oracle(
    artifact_root: Path, artifact_manifest: Mapping[str, Any], model_id: str
) -> dict[str, Any]:
    reference = artifact_manifest["correctness_oracles"][model_id]
    path = artifact_root / str(reference["path"])
    if sha256_file(path) != reference["sha256"]:
        raise X1ExperimentError(f"x1_oracle_sha:{model_id}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("model_id") != model_id:
        raise X1ExperimentError(f"x1_oracle_identity:{model_id}")
    return payload


def _direct_infer(
    model_id: str,
    features: Sequence[float],
    *,
    request_id: str,
    session: requests.Session,
) -> float:
    try:
        response = session.post(
            f"{TRITON_URL}/v2/models/{model_id}/versions/1/infer",
            json=_direct_infer_payload(features, request_id=request_id),
            timeout=10,
        )
        response.raise_for_status()
    except requests.RequestException as exc:
        raise X1ExperimentError(
            f"x1_q0_transport:{model_id}:{request_id}:{type(exc).__name__}"
        ) from exc
    outputs = response.json().get("outputs")
    if not isinstance(outputs, list) or len(outputs) != 1:
        raise X1ExperimentError("x1_q0_output_schema")
    values = outputs[0].get("data")
    if not isinstance(values, list) or len(values) != 1:
        raise X1ExperimentError("x1_q0_output_cardinality")
    value = values[0]
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise X1ExperimentError("x1_q0_output_value")
    return float(value)


def _direct_infer_payload(features: Sequence[float], *, request_id: str) -> dict[str, Any]:
    return {
        "id": request_id,
        "inputs": [
            {
                "name": "INPUT__0",
                "shape": [len(features)],
                "datatype": "FP32",
                "data": list(features),
            }
        ],
        "outputs": [{"name": "OUTPUT__0"}],
    }


def _bounded_command_snapshot(command: Sequence[str], *, timeout: float = 30) -> dict[str, Any]:
    try:
        result = run(command, check=False, timeout=timeout)
        return {
            "command": list(command),
            "exit_code": result.returncode,
            "stdout": result.stdout[-200_000:],
            "stderr": result.stderr[-200_000:],
        }
    except Exception as exc:  # noqa: BLE001 - preserve diagnostic failure
        return {
            "command": list(command),
            "error_type": type(exc).__name__,
            "error": str(exc),
        }


def _bounded_http_snapshot(
    session: requests.Session,
    method: str,
    url: str,
    *,
    payload: Mapping[str, Any] | None = None,
    timeout: float = 10,
) -> dict[str, Any]:
    started = time.perf_counter()
    try:
        response = session.request(method, url, json=payload, timeout=timeout)
        body = response.text
        return {
            "url": url,
            "method": method,
            "status_code": response.status_code,
            "elapsed_ms": (time.perf_counter() - started) * 1000,
            "headers": dict(sorted(response.headers.items())),
            "body_sha256": hashlib.sha256(body.encode("utf-8")).hexdigest(),
            "body": body[-200_000:],
        }
    except requests.RequestException as exc:
        return {
            "url": url,
            "method": method,
            "elapsed_ms": (time.perf_counter() - started) * 1000,
            "error_type": type(exc).__name__,
            "error": str(exc),
        }


def capture_q0_transport_diagnostic(
    *,
    suite_id: str,
    model_id: str,
    request_id: str,
    features: Sequence[float],
    original_error: str,
) -> dict[str, Any]:
    diagnostic: dict[str, Any] = {
        "schema_version": "evm.s8_v4.x1_q0_transport_diagnostic.v1",
        "suite_id": suite_id,
        "captured_at": utc_now(),
        "credit": "zero_credit",
        "acceptance_credit": False,
        "scope": "failure-time transport localization only; never Q0 retry credit",
        "failed_request": {
            "model_id": model_id,
            "request_id": request_id,
            "original_error": original_error,
            "payload_sha256": canonical_sha256(
                _direct_infer_payload(features, request_id=request_id)
            ),
        },
        "commands": {
            "service": _bounded_command_snapshot(
                ["kubectl", "-n", NAMESPACE, "get", f"service/{TRITON_NAME}", "-o", "json"]
            ),
            "endpoints": _bounded_command_snapshot(
                ["kubectl", "-n", NAMESPACE, "get", f"endpoints/{TRITON_NAME}", "-o", "json"]
            ),
            "pods": _bounded_command_snapshot(
                [
                    "kubectl",
                    "-n",
                    NAMESPACE,
                    "get",
                    "pods",
                    "-l",
                    f"app.kubernetes.io/name={TRITON_NAME}",
                    "-o",
                    "json",
                ]
            ),
            "triton_logs": _bounded_command_snapshot(
                ["kubectl", "-n", NAMESPACE, "logs", f"deployment/{TRITON_NAME}", "--tail=500"]
            ),
            "windows_tcp": _bounded_command_snapshot(["netstat", "-ano", "-p", "tcp"]),
        },
    }
    with requests.Session() as session:
        session.trust_env = False
        diagnostic["nodeport_after_failure"] = {
            "live": _bounded_http_snapshot(
                session, "GET", f"{TRITON_URL}/v2/health/live", timeout=2
            ),
            "ready": _bounded_http_snapshot(
                session, "GET", f"{TRITON_URL}/v2/health/ready", timeout=2
            ),
            "metrics": _bounded_http_snapshot(session, "GET", TRITON_METRICS_URL, timeout=5),
        }

    process: subprocess.Popen[str] | None = None
    stdout = ""
    stderr = ""
    try:
        if port_open(Q0_DIAGNOSTIC_HTTP_PORT) or port_open(Q0_DIAGNOSTIC_METRICS_PORT):
            raise X1ExperimentError("x1_q0_diagnostic_port_in_use")
        process = subprocess.Popen(
            [
                "kubectl",
                "-n",
                NAMESPACE,
                "port-forward",
                f"deployment/{TRITON_NAME}",
                f"{Q0_DIAGNOSTIC_HTTP_PORT}:8000",
                f"{Q0_DIAGNOSTIC_METRICS_PORT}:8002",
                "--address=127.0.0.1",
            ],
            cwd=ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if process.poll() is not None:
                break
            if port_open(Q0_DIAGNOSTIC_HTTP_PORT) and port_open(Q0_DIAGNOSTIC_METRICS_PORT):
                break
            time.sleep(0.1)
        ready = (
            process.poll() is None
            and port_open(Q0_DIAGNOSTIC_HTTP_PORT)
            and port_open(Q0_DIAGNOSTIC_METRICS_PORT)
        )
        diagnostic["port_forward_ready"] = ready
        if ready:
            with requests.Session() as session:
                session.trust_env = False
                diagnostic["port_forward"] = {
                    "live": _bounded_http_snapshot(
                        session,
                        "GET",
                        f"http://127.0.0.1:{Q0_DIAGNOSTIC_HTTP_PORT}/v2/health/live",
                        timeout=2,
                    ),
                    "infer": _bounded_http_snapshot(
                        session,
                        "POST",
                        (
                            f"http://127.0.0.1:{Q0_DIAGNOSTIC_HTTP_PORT}/v2/models/"
                            f"{model_id}/versions/1/infer"
                        ),
                        payload=_direct_infer_payload(
                            features, request_id=f"{request_id}-portforward-diagnostic"
                        ),
                        timeout=10,
                    ),
                    "metrics": _bounded_http_snapshot(
                        session,
                        "GET",
                        f"http://127.0.0.1:{Q0_DIAGNOSTIC_METRICS_PORT}/metrics",
                        timeout=5,
                    ),
                }
    except Exception as exc:  # noqa: BLE001 - preserve diagnostic failure
        diagnostic["port_forward_error"] = {
            "error_type": type(exc).__name__,
            "error": str(exc),
        }
    finally:
        if process is not None:
            if process.poll() is None:
                process.terminate()
            try:
                stdout, stderr = process.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                stdout, stderr = process.communicate(timeout=5)
            diagnostic["port_forward_process"] = {
                "exit_code": process.returncode,
                "stdout": stdout[-20_000:],
                "stderr": stderr[-20_000:],
            }
    diagnostic["port_forward_ports_released"] = not (
        port_open(Q0_DIAGNOSTIC_HTTP_PORT) or port_open(Q0_DIAGNOSTIC_METRICS_PORT)
    )
    return diagnostic


def run_q0(
    contract: X1Contract,
    *,
    suite_id: str,
    artifact_root: Path,
    artifact_manifest: Mapping[str, Any],
) -> dict[str, Any]:
    index = repository_index()
    records: list[dict[str, Any]] = []
    with requests.Session() as session:
        session.trust_env = False
        for model_id in MODEL_IDS:
            oracle = _oracle(artifact_root, artifact_manifest, model_id)
            config = triton_config_readback(model_id)
            validate_triton_runtime_config(
                config,
                model_id=model_id,
                identity={
                    **artifact_manifest["models"][model_id],
                    "feature_count": 39 if model_id == "criteo_dlrm_lite" else 28,
                    "max_batch_size": 0,
                    "preferred_batch_size": [],
                    "max_queue_delay_microseconds": 0,
                },
            )
            before = triton_metrics_text()
            gpu_before = capture_gpu()
            correct = 0
            for sequence, (features, expected) in enumerate(
                zip(oracle["input"], oracle["output"], strict=True)
            ):
                request_id = f"{suite_id}-q0-{model_id}-{sequence:04d}"
                try:
                    observed = _direct_infer(
                        model_id,
                        features,
                        request_id=request_id,
                        session=session,
                    )
                except X1ExperimentError as exc:
                    if str(exc).startswith("x1_q0_transport:"):
                        diagnostic = capture_q0_transport_diagnostic(
                            suite_id=suite_id,
                            model_id=model_id,
                            request_id=request_id,
                            features=features,
                            original_error=str(exc),
                        )
                        canonical_write(
                            artifact_root.parent / "q0-transport-diagnostic.json",
                            diagnostic,
                        )
                    raise
                expected_value = float(expected[0])
                if math.isclose(
                    observed,
                    expected_value,
                    rel_tol=float(oracle["relative_tolerance"]),
                    abs_tol=float(oracle["absolute_tolerance"]),
                ):
                    correct += 1
            after = triton_metrics_text()
            gpu_after = capture_gpu()
            delta = _metric_delta(before, after, model_id)
            identity = artifact_manifest["models"][model_id]
            config_path = artifact_root / "model-repositories/disabled" / model_id / "config.pbtxt"
            actual_cuda = (
                delta["success_count"] == 64
                and delta["inference_count"] == 64
                and int(delta["execution_count"]) > 0
                and float(delta["compute_duration_us"]) > 0
                and gpu_before["uuid"] == GPU_UUID
                and gpu_after["uuid"] == GPU_UUID
            )
            records.append(
                {
                    "model_id": model_id,
                    "model_version": "1",
                    "artifact_sha256": identity["artifact_sha256"],
                    "isolated_request_count": 64,
                    "correct_count": correct,
                    "failed_count": 64 - correct,
                    "cpu_fallback_detected": not actual_cuda,
                    "actual_cuda_activity": actual_cuda,
                    "triton_delta": delta,
                    "gpu_uuid": gpu_after["uuid"],
                    "gpu_name": gpu_after["name"],
                    "config_sha256": sha256_file(config_path),
                    "config_bytes_sha256": sha256_file(config_path),
                    "repository_index_exact": index == sorted(index, key=lambda item: item["name"]),
                    "runtime_config_readback": config,
                    "gpu_before": gpu_before,
                    "gpu_after": gpu_after,
                }
            )
    bundle = {
        "schema_version": "evm.s8_v4.x1_q0.v1",
        "suite_id": suite_id,
        "captured_at": utc_now(),
        "repository_index": index,
        "models": records,
    }
    bundle["projection"] = validate_q0_bundle(bundle, contract, artifact_manifest)
    return bundle


def _otlp_value(payload: Mapping[str, Any]) -> Any:
    names = ("stringValue", "boolValue", "intValue", "doubleValue")
    present = [name for name in names if name in payload]
    return payload[present[0]] if len(present) == 1 else None


def _otlp_attributes(value: Any) -> dict[str, Any]:
    if not isinstance(value, list):
        return {}
    return {
        str(item.get("key")): _otlp_value(item.get("value", {}))
        for item in value
        if isinstance(item, Mapping) and isinstance(item.get("value"), Mapping)
    }


def _iter_otlp_entries(path: Path, *, offset: int) -> list[dict[str, Any]]:
    if not path.is_file():
        raise X1ExperimentError("x1_otel_file_absent")
    snapshot_size = path.stat().st_size
    if offset < 0 or offset > snapshot_size:
        raise X1ExperimentError("x1_otel_offset")
    entries: list[dict[str, Any]] = []
    with path.open("rb") as handle:
        if offset:
            handle.seek(offset - 1)
            if handle.read(1) != b"\n":
                handle.readline()
        else:
            handle.seek(0)
        for line in handle.read(snapshot_size - offset).splitlines(keepends=True):
            if not line.endswith(b"\n"):
                continue
            try:
                batch = json.loads(line.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise X1ExperimentError("x1_otel_json") from exc
            for resource_spans in batch.get("resourceSpans", []):
                resource = dict(resource_spans.get("resource", {}))
                for scope_spans in resource_spans.get("scopeSpans", []):
                    scope = dict(scope_spans.get("scope", {}))
                    for span in scope_spans.get("spans", []):
                        entries.append({"resource": resource, "scope": scope, "span": dict(span)})
    return entries


def collect_trace_export(
    *,
    path: Path,
    offset: int,
    runtime_attempt_id: str,
    expected_completed: int,
    timeout: float = 45,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    matches: list[dict[str, Any]] = []
    while time.monotonic() < deadline:
        entries = _iter_otlp_entries(path, offset=offset)
        matches = [
            entry
            for entry in entries
            if _otlp_attributes(entry["span"].get("attributes", [])).get("evm.x1.attempt_id")
            == runtime_attempt_id
            and _otlp_attributes(entry["span"].get("attributes", [])).get("evm.x1.request_id")
            is not None
        ]
        if len(matches) == expected_completed:
            break
        time.sleep(0.5)
    if len(matches) != expected_completed:
        raise X1ExperimentError(
            f"x1_otel_convergence:{runtime_attempt_id}:{len(matches)}/{expected_completed}"
        )
    return {
        "schema_version": "evm.s8_v4.x1_raw_otlp_export.v1",
        "attempt_id": runtime_attempt_id,
        "collector_start_offset": offset,
        "entry_count": len(matches),
        "entries": matches,
    }


def deterministic_model_schedule(weights: Mapping[str, float], count: int) -> list[str]:
    if (
        set(weights) - set(MODEL_IDS)
        or count < 1
        or any(not math.isfinite(value) or value <= 0 for value in weights.values())
    ):
        raise X1ExperimentError("x1_model_schedule_input")
    ordered = [model_id for model_id in MODEL_IDS if model_id in weights]
    total = sum(weights.values())
    normalized = {model_id: weights[model_id] / total for model_id in ordered}
    emitted = {model_id: 0 for model_id in ordered}
    schedule: list[str] = []
    for sequence in range(count):
        selected = max(
            ordered,
            key=lambda model_id: (
                (sequence + 1) * normalized[model_id] - emitted[model_id],
                -ordered.index(model_id),
            ),
        )
        schedule.append(selected)
        emitted[selected] += 1
    return schedule


def _traceparent(request_id: str) -> str:
    trace_id = hashlib.sha256(f"trace:{request_id}".encode("ascii")).hexdigest()[:32]
    span_id = hashlib.sha256(f"span:{request_id}".encode("ascii")).hexdigest()[:16]
    return f"00-{trace_id}-{span_id}-01"


def _response_error(payload: Any) -> str:
    if isinstance(payload, Mapping):
        detail = payload.get("detail")
        if isinstance(detail, Mapping) and isinstance(detail.get("error"), str):
            return str(detail["error"])
    return "http_error"


def _send_request(
    *,
    suite_id: str,
    runtime_attempt_id: str,
    request_id: str,
    model_id: str,
    features: Sequence[float],
    identity: Mapping[str, Any],
    lease: GpuLease,
    enqueued_ns: int,
    deadline_seconds: float,
) -> dict[str, Any]:
    started_ns = time.perf_counter_ns()
    traceparent = _traceparent(request_id)
    body = {
        "schema_version": "evm.s8_v4.x1_inference_request.v1",
        "suite_id": suite_id,
        "attempt_id": runtime_attempt_id,
        "request_id": request_id,
        "traceparent": traceparent,
        "model_id": model_id,
        "model_version": "1",
        "artifact_sha256": identity["artifact_sha256"],
        "config_sha256": identity["config_sha256"],
        "features": [float(value) for value in features],
        "deadline_unix_ns": time.time_ns() + int(deadline_seconds * 1_000_000_000),
        "lease_id": lease.lease_id,
        "fencing_token": lease.fencing_token,
    }
    try:
        response = requests.post(
            f"{API_URL}/control-panel/v1/scenario-workloads/x1/predict",
            json=body,
            headers={"traceparent": traceparent, "Connection": "close"},
            timeout=deadline_seconds + 1,
        )
        finished_ns = time.perf_counter_ns()
        try:
            payload = response.json()
        except requests.JSONDecodeError:
            payload = {}
        base = {
            "request_id": request_id,
            "model_id": model_id,
            "enqueued_ns": enqueued_ns,
            "started_ns": started_ns,
            "finished_ns": finished_ns,
            "latency_ms": (finished_ns - started_ns) / 1_000_000,
            "client_permit_wait_ms": (started_ns - enqueued_ns) / 1_000_000,
            "status_code": response.status_code,
        }
        if response.status_code == 200 and isinstance(payload, Mapping):
            expected_trace_id = traceparent.split("-")[1]
            if (
                payload.get("request_id") != request_id
                or payload.get("attempt_id") != runtime_attempt_id
                or payload.get("suite_id") != suite_id
                or payload.get("model_id") != model_id
                or payload.get("trace_id") != expected_trace_id
            ):
                raise X1ExperimentError("x1_response_identity")
            return {
                **base,
                **dict(payload),
                "admission_outcome": "accepted",
                "rejection_reason": "",
                "queue_wait_ms": float(payload["queue_wait_ms"]),
                "prediction_ms": float(payload["prediction_ms"]),
                "oom_detected": False,
                "outcome_unknown": False,
            }
        reason = _response_error(payload)
        if response.status_code == 429 and reason == "x1_admission_rejected":
            return {
                **base,
                "admission_outcome": "rejected",
                "rejection_reason": reason,
            }
        return {
            **base,
            "admission_outcome": "accepted",
            "rejection_reason": "",
            "terminal_outcome": "failed",
            "oom_detected": "oom" in reason.lower(),
            "outcome_unknown": False,
            "failure_reason": reason,
        }
    except requests.RequestException as exc:
        finished_ns = time.perf_counter_ns()
        return {
            "request_id": request_id,
            "model_id": model_id,
            "enqueued_ns": enqueued_ns,
            "started_ns": started_ns,
            "finished_ns": finished_ns,
            "latency_ms": (finished_ns - started_ns) / 1_000_000,
            "client_permit_wait_ms": (started_ns - enqueued_ns) / 1_000_000,
            "status_code": 0,
            "admission_outcome": "accepted",
            "rejection_reason": "",
            "terminal_outcome": "failed",
            "oom_detected": False,
            "outcome_unknown": True,
            "failure_reason": type(exc).__name__,
        }


def _local_rejection(*, request_id: str, model_id: str, enqueued_ns: int) -> dict[str, Any]:
    return {
        "request_id": request_id,
        "model_id": model_id,
        "enqueued_ns": enqueued_ns,
        "admission_outcome": "rejected",
        "rejection_reason": "client_outstanding_bound",
    }


def run_traffic_window(
    contract: X1Contract,
    *,
    suite_id: str,
    outer_attempt_id: str,
    runtime_attempt_id: str,
    step_index: int,
    offered_rps: int,
    duration_seconds: int,
    model_weights: Mapping[str, float],
    artifact_manifest: Mapping[str, Any],
    artifact_root: Path,
    lease: GpuLease,
    phase: str,
) -> dict[str, Any]:
    total = offered_rps * duration_seconds
    schedule = deterministic_model_schedule(model_weights, total)
    model_inputs = {
        model_id: _oracle(artifact_root, artifact_manifest, model_id)["input"]
        for model_id in model_weights
    }
    model_identities = artifact_manifest["models"]
    maximum_outstanding = int(contract.payload["calibration"]["max_outstanding"])
    deadline_seconds = float(contract.payload["calibration"]["request_deadline_seconds"])
    started_window_ns = time.perf_counter_ns()
    ended_window_ns = started_window_ns + duration_seconds * 1_000_000_000
    pending: dict[concurrent.futures.Future[dict[str, Any]], int] = {}
    records: dict[int, dict[str, Any]] = {}
    request_prefix = "w" if phase == "warmup" else "s"
    with concurrent.futures.ThreadPoolExecutor(
        max_workers=int(contract.payload["topology"]["client_driver_workers"]),
        thread_name_prefix="x1-client",
    ) as executor:
        for sequence, model_id in enumerate(schedule):
            target_ns = started_window_ns + (sequence * 1_000_000_000) // offered_rps
            delay = (target_ns - time.perf_counter_ns()) / 1_000_000_000
            if delay > 0:
                time.sleep(delay)
            for future in tuple(pending):
                if future.done():
                    records[pending.pop(future)] = future.result()
            enqueued_ns = time.perf_counter_ns()
            request_id = f"{outer_attempt_id}-{request_prefix}{step_index:02d}-{sequence:08d}"
            if len(pending) >= maximum_outstanding:
                records[sequence] = _local_rejection(
                    request_id=request_id,
                    model_id=model_id,
                    enqueued_ns=enqueued_ns,
                )
                continue
            inputs = model_inputs[model_id]
            features = inputs[sequence % len(inputs)]
            future = executor.submit(
                _send_request,
                suite_id=suite_id,
                runtime_attempt_id=runtime_attempt_id,
                request_id=request_id,
                model_id=model_id,
                features=features,
                identity={
                    **model_identities[model_id],
                    "config_sha256": _active_config_sha(
                        artifact_manifest,
                        model_id=model_id,
                        profile_id=_active_profile_from_attempt(outer_attempt_id),
                    ),
                },
                lease=lease,
                enqueued_ns=enqueued_ns,
                deadline_seconds=deadline_seconds,
            )
            pending[future] = sequence
        for future in concurrent.futures.as_completed(pending):
            records[pending[future]] = future.result()
    if len(records) != total or set(records) != set(range(total)):
        raise X1ExperimentError("x1_traffic_record_set")
    ordered = [records[index] for index in range(total)]
    if phase == "measurement" and any(
        int(record["enqueued_ns"]) < started_window_ns
        or int(record["enqueued_ns"]) >= ended_window_ns
        for record in ordered
    ):
        raise X1ExperimentError("x1_traffic_schedule_window")
    return {
        "phase": phase,
        "offered_rps": offered_rps,
        "offered_count": total,
        "model_schedule_sha256": canonical_sha256(schedule),
        "model_schedule_counts": dict(Counter(schedule)),
        "window": {"start_ns": started_window_ns, "end_ns": ended_window_ns},
        "requests": ordered,
    }


def _active_profile_from_attempt(attempt_id: str) -> str:
    for profile_id in PROFILE_IDS:
        token = profile_id.replace("-", "")
        if token in attempt_id.replace("-", ""):
            return profile_id
    return "disabled"


def _active_config_sha(
    artifact_manifest: Mapping[str, Any], *, model_id: str, profile_id: str
) -> str:
    entries = artifact_manifest["repositories"][profile_id]["entries"]
    matches = [entry["sha256"] for entry in entries if entry["path"] == f"{model_id}/config.pbtxt"]
    if len(matches) != 1:
        raise X1ExperimentError("x1_active_config_identity")
    return str(matches[0])


def sample_gpu(stop: threading.Event, output: list[dict[str, Any]], interval: float = 0.25) -> None:
    while not stop.is_set():
        sample = capture_gpu()
        output.append(
            {
                "captured_at": utc_now(),
                "gpu_uuid": sample["uuid"],
                "gpu_name": sample["name"],
                "utilization_percent": sample["utilization_percent"],
                "memory_used_mib": sample["memory_used_mib"],
                "memory_total_mib": sample["memory_total_mib"],
                "temperature_celsius": sample["temperature_celsius"],
                "power_watts": sample["power_watts"],
            }
        )
        stop.wait(interval)


def export_effects(runtime_attempt_id: str) -> list[dict[str, Any]]:
    response = requests.get(
        f"{API_URL}/control-panel/v1/scenario-workloads/x1/effects/{runtime_attempt_id}",
        timeout=30,
    )
    response.raise_for_status()
    payload = response.json()
    if (
        payload.get("schema_version") != "evm.s8_v4.x1_terminal_effect_export.v1"
        or payload.get("attempt_id") != runtime_attempt_id
        or payload.get("effect_count") != len(payload.get("effects", []))
    ):
        raise X1ExperimentError("x1_effect_export_identity")
    return list(payload["effects"])


def validate_warmup(window: Mapping[str, Any], effects: Sequence[Mapping[str, Any]]) -> None:
    records = window.get("requests")
    if not isinstance(records, list) or not records:
        raise X1ExperimentError("x1_warmup_records")
    completed = [
        record
        for record in records
        if record.get("admission_outcome") == "accepted"
        and record.get("status_code") == 200
        and record.get("terminal_outcome") == "completed"
        and record.get("outcome_unknown") is False
        and record.get("oom_detected") is False
    ]
    accepted = [record for record in records if record.get("admission_outcome") == "accepted"]
    if not completed or len(completed) != len(accepted):
        raise X1ExperimentError("x1_warmup_terminal_invariant")
    request_ids = {str(record["request_id"]) for record in completed}
    effect_ids = {
        str(effect.get("payload", {}).get("request_id"))
        for effect in effects
        if isinstance(effect, Mapping) and isinstance(effect.get("payload"), Mapping)
    }
    if effect_ids != request_ids or len(effects) != len(request_ids):
        raise X1ExperimentError("x1_warmup_effect_join")


def _response_topology(records: Sequence[Mapping[str, Any]]) -> dict[str, set[str]]:
    result: dict[str, set[str]] = {}
    for record in records:
        if record.get("admission_outcome") != "accepted" or record.get("status_code") != 200:
            continue
        topology = record.get("topology")
        if not isinstance(topology, Mapping):
            raise X1ExperimentError("x1_response_topology_schema")
        pod_uid = str(topology.get("pod_uid", ""))
        worker_slot = str(topology.get("worker_slot", ""))
        if not pod_uid or not worker_slot:
            raise X1ExperimentError("x1_response_topology_identity")
        result.setdefault(pod_uid, set()).add(worker_slot)
    return result


def run_calibration_attempt(
    contract: X1Contract,
    *,
    suite_root: Path,
    suite_id: str,
    cell_id: str,
    mode: str,
    model_id: str | None,
    repetition: int,
    api_replicas: int,
    cpu_workers: int,
    model_weights: Mapping[str, float],
    artifact_manifest: Mapping[str, Any],
    artifact_root: Path,
    lease: GpuLease,
    batch_candidate: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    attempt_id = f"x1-{cell_id}"
    steps: list[dict[str, Any]] = []
    topology_workers: dict[str, set[str]] = {}
    for step_index, offered_rps in enumerate(contract.payload["calibration"]["arrival_steps_rps"]):
        warmup_attempt_id = f"{attempt_id}-warmup-{step_index:02d}"
        warmup = run_traffic_window(
            contract,
            suite_id=suite_id,
            outer_attempt_id=attempt_id,
            runtime_attempt_id=warmup_attempt_id,
            step_index=step_index,
            offered_rps=int(offered_rps),
            duration_seconds=int(contract.payload["calibration"]["warmup_seconds"]),
            model_weights=model_weights,
            artifact_manifest=artifact_manifest,
            artifact_root=artifact_root,
            lease=lease,
            phase="warmup",
        )
        warmup_effects = export_effects(warmup_attempt_id)
        validate_warmup(warmup, warmup_effects)
        runtime_attempt_id = f"{attempt_id}-step-{step_index:02d}"
        metrics_before = triton_metrics_text()
        trace_offset = OTEL_TRACE_FILE.stat().st_size
        gpu_samples: list[dict[str, Any]] = []
        stop = threading.Event()
        sampler = threading.Thread(
            target=sample_gpu,
            args=(stop, gpu_samples),
            name=f"x1-gpu-{cell_id}-{step_index}",
            daemon=True,
        )
        sampler.start()
        try:
            measured = run_traffic_window(
                contract,
                suite_id=suite_id,
                outer_attempt_id=attempt_id,
                runtime_attempt_id=runtime_attempt_id,
                step_index=step_index,
                offered_rps=int(offered_rps),
                duration_seconds=int(contract.payload["calibration"]["measurement_seconds"]),
                model_weights=model_weights,
                artifact_manifest=artifact_manifest,
                artifact_root=artifact_root,
                lease=lease,
                phase="measurement",
            )
        finally:
            stop.set()
            sampler.join(timeout=5)
        metrics_after = triton_metrics_text()
        effects = export_effects(runtime_attempt_id)
        completed = sum(
            record.get("admission_outcome") == "accepted"
            and record.get("status_code") == 200
            and record.get("terminal_outcome") == "completed"
            for record in measured["requests"]
        )
        traces = collect_trace_export(
            path=OTEL_TRACE_FILE,
            offset=trace_offset,
            runtime_attempt_id=runtime_attempt_id,
            expected_completed=completed,
        )
        for pod_uid, slots in _response_topology(measured["requests"]).items():
            topology_workers.setdefault(pod_uid, set()).update(slots)
        steps.append(
            {
                "runtime_attempt_id": runtime_attempt_id,
                "offered_rps": int(offered_rps),
                "measurement_window": measured["window"],
                "requests": measured["requests"],
                "durable_effects": effects,
                "trace_export": traces,
                "triton_metrics": {
                    "model_ids": sorted(model_weights, key=MODEL_IDS.index),
                    "before_raw": metrics_before,
                    "after_raw": metrics_after,
                },
                "gpu_samples": gpu_samples,
                "warmup": {**warmup, "durable_effects": warmup_effects},
            }
        )
        time.sleep(int(contract.payload["calibration"]["cooldown_seconds"]))
    readback = capture_topology_readback(
        expected_replicas=api_replicas,
        expected_workers=cpu_workers,
    )
    readback["observed_worker_slots_by_pod"] = {
        pod_uid: sorted(topology_workers.get(pod_uid, set()))
        for pod_uid in readback["observed_api_pod_uids"]
    }
    validate_runtime_topology_readback(
        readback,
        expected_replicas=api_replicas,
        expected_workers=cpu_workers,
    )
    raw = {
        "schema_version": "evm.s8_v4.x1_calibration_attempt.v1",
        "suite_id": suite_id,
        "attempt_id": attempt_id,
        "mode": mode,
        "model_id": model_id,
        "repetition": repetition,
        "topology": {"api_replicas": api_replicas, "cpu_workers": cpu_workers},
        "topology_readback": readback,
        "model_weights": dict(model_weights),
        "steps": steps,
    }
    projection = project_calibration_attempt(raw, contract)
    if batch_candidate is not None:
        projection["batch_candidate"] = batch_candidate
        projection["guardrails_passed"] = _guardrails_pass(projection, contract)
    raw_path = suite_root / "calibration" / mode / f"{cell_id}.json"
    canonical_write(raw_path, raw)
    projection_path = suite_root / "projections" / mode / f"{cell_id}.json"
    canonical_write(projection_path, projection)
    return raw, projection


def private_index(root: Path) -> dict[str, Any]:
    entries: list[dict[str, Any]] = []
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
    return {
        "schema_version": "evm.s8_v4.x1_private_index.v1",
        "artifact_count": len(entries),
        "total_bytes": sum(entry["bytes"] for entry in entries),
        "aggregate_sha256": canonical_sha256(entries),
        "entries": entries,
    }


def release_lease(lease: GpuLease | None, *, reason: str) -> dict[str, Any] | None:
    if lease is None or read_active_gpu_lease() is None:
        return None
    released = release_scale_validation_gpu_lease(
        run_id=lease.run_id,
        lease_id=lease.lease_id,
        fencing_token=lease.fencing_token,
        reason=reason,
    )
    return released.model_dump(mode="json")


def cleanup_snapshot(
    *,
    holder: Holder,
    gpu_before: Mapping[str, Any],
    prometheus_before: Mapping[str, Any],
    database_schema: str,
) -> dict[str, Any]:
    current = capture_holder()
    b0 = b0_cuda_inference()
    gpu_after = capture_gpu()
    queues = queue_counts()
    prometheus = wait_prometheus_baseline(prometheus_before, timeout=120)
    tolerance = max(256.0, float(gpu_before["memory_total_mib"]) * 0.05)
    payload = {
        "schema_version": "evm.s8_v4.x1_cleanup.v1",
        "captured_at": utc_now(),
        "b0_uid_exact": current.uid == holder.uid,
        "b0_image_exact": current.image == holder.image,
        "b0_ready_1_of_1": current.replicas == 1,
        "b0_actual_cuda": b0["passed"] is True,
        "prometheus": prometheus,
        "prometheus_5_of_5": prometheus.get("total") == 5 and prometheus.get("up") == 5,
        "queues": queues,
        "gpu_lease_absent": read_active_gpu_lease() is None,
        "runtime_absent": x1_runtime_absent(),
        "database_schema_absent": not database_schema_exists(database_schema),
        "gpu_before": dict(gpu_before),
        "gpu_after": gpu_after,
        "vram_delta_mib": float(gpu_after["memory_used_mib"])
        - float(gpu_before["memory_used_mib"]),
        "vram_restored": abs(
            float(gpu_after["memory_used_mib"]) - float(gpu_before["memory_used_mib"])
        )
        <= tolerance,
    }
    if not all(
        (
            payload["b0_uid_exact"],
            payload["b0_image_exact"],
            payload["b0_ready_1_of_1"],
            payload["b0_actual_cuda"],
            payload["prometheus_5_of_5"],
            not any(queues.values()),
            payload["gpu_lease_absent"],
            payload["runtime_absent"],
            payload["database_schema_absent"],
            payload["vram_restored"],
        )
    ):
        raise X1ExperimentError(f"x1_cleanup_failed:{payload}")
    return payload


def _weights_from_solo(projections: Sequence[Mapping[str, Any]]) -> dict[str, float]:
    grouped: dict[str, list[float]] = {model_id: [] for model_id in MODEL_IDS}
    for projection in projections:
        model_id = str(projection.get("model_id"))
        if model_id not in grouped:
            raise X1ExperimentError("x1_solo_projection_model")
        value = float(projection["gpu_seconds_per_request"])
        if not math.isfinite(value) or value <= 0:
            raise X1ExperimentError("x1_solo_gpu_demand")
        grouped[model_id].append(value)
    if any(len(values) != 3 for values in grouped.values()):
        raise X1ExperimentError("x1_solo_projection_repetitions")
    demands = {model_id: max(values) for model_id, values in grouped.items()}
    return {model_id: 0.25 / demands[model_id] for model_id in MODEL_IDS}


def _guardrails_pass(projection: Mapping[str, Any], contract: X1Contract) -> bool:
    maximum_queue = float(contract.payload["guardrails"]["maximum_queue_wait_ms"])
    return all(
        step["error_rate"] <= float(contract.payload["guardrails"]["maximum_error_rate"])
        and step["p99_ms"] <= float(contract.payload["guardrails"]["maximum_p99_ms"])
        and step["queue_wait_p99_ms"] <= maximum_queue
        and step["lost"] == 0
        and step["duplicate_effects"] == 0
        and step["outcome_unknown"] == 0
        and step["silent_fallback"] == 0
        and step["unexpected_oom"] == 0
        for step in projection["steps"]
        if step["offered_rps"] <= projection["selected_offered_rps"]
    )


def _public_projection(
    *,
    suite_id: str,
    source: Mapping[str, Any],
    contract: X1Contract,
    artifact_manifest_path: Path,
    q0_path: Path,
    solo: Sequence[Mapping[str, Any]],
    topology: Sequence[Mapping[str, Any]],
    batching: Sequence[Mapping[str, Any]],
    selected_batching: Mapping[str, str],
    load_freeze: Mapping[str, Any],
    cleanup: Mapping[str, Any],
    index: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "schema_version": "evm.s8_v4.x1_load_freeze_evidence.v1",
        "suite_id": suite_id,
        "status": "load_freeze_ready",
        "credit": "non_credit",
        "acceptance_credit": False,
        "source": dict(source),
        "source_blobs": source_blob_inventory(
            ROOT,
            str(source["revision"]),
            require_worktree_match=True,
        ),
        "contract": {
            "path": contract.path.relative_to(ROOT).as_posix(),
            "sha256": contract.sha256,
        },
        "artifact_manifest": {
            "path": str(artifact_manifest_path),
            "sha256": sha256_file(artifact_manifest_path),
        },
        "q0": {"models_passed": 4, "requests_passed": 256, "sha256": sha256_file(q0_path)},
        "calibration": {
            "solo_repetitions": len(solo),
            "topology_repetitions": len(topology),
            "batching_repetitions": len(batching),
            "selected_batching": dict(selected_batching),
            "load_freeze": dict(load_freeze),
        },
        "profiler": {
            "frozen_mode": "concurrent_balanced",
            "frozen_topology": "r1-w4",
            "qualification_repetitions": 3,
            "verdict": KERNEL_OVERLAP_FALLBACK,
            "claim": "No CUDA kernel overlap claim without model/request-attributed Nsight/CUPTI intervals.",
        },
        "cleanup": dict(cleanup),
        "private_evidence": {
            "artifact_count": index["artifact_count"],
            "total_bytes": index["total_bytes"],
            "aggregate_sha256": index["aggregate_sha256"],
            "index_sha256": index["index_sha256"],
        },
        "execution_boundary": {
            "credit_matrix_started": False,
            "integrated_v4_started": False,
            "next_gate": "independent_review_of_contract_and_load_freeze",
        },
        "claim_boundary": contract.payload["claim"]["boundary"],
    }


def main() -> int:
    args = parse_args()
    if not args.maintenance_approved:
        raise X1ExperimentError("x1_maintenance_approval_required")
    contract = X1Contract.from_path(
        args.config,
        source_root=ROOT,
        data_root=args.data_root,
    )
    if args.public_output.exists():
        raise X1ExperimentError(f"x1_public_output_exists:{args.public_output}")
    environment = preflight(contract)
    source = environment["source"]
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ").lower()
    suite_id = f"x1-canonical-{timestamp}-{uuid4().hex[:8]}"
    suite_root = args.private_base / suite_id
    suite_root.mkdir(parents=True, exist_ok=False)
    canonical_write(suite_root / "environment-preflight.json", environment)
    artifact_root = suite_root / "artifacts"
    database_schema = f"evm_x1_v1_{hashlib.sha256(suite_id.encode('ascii')).hexdigest()[:12]}"
    holder = Holder(**environment["holder"])
    gpu_before = environment["gpu"]
    prometheus_before = environment["prometheus"]
    api_image = build_api_image(source["revision"])
    holder_scaled = False
    target_written = False
    topology_started = False
    training_lease: GpuLease | None = None
    inference_lease: GpuLease | None = None
    failure: dict[str, Any] | None = None
    cleanup_errors: list[dict[str, str]] = []
    cleanup: dict[str, Any] | None = None
    artifact_manifest_path: Path | None = None
    q0_path: Path | None = None
    solo_projections: list[dict[str, Any]] = []
    topology_projections: list[dict[str, Any]] = []
    batching_projections: list[dict[str, Any]] = []
    selected_batching: dict[str, str] = {}
    load_freeze: dict[str, Any] = {}
    try:
        scale_holder(holder, 0)
        holder_scaled = True
        training_lease = acquire_scale_validation_gpu_lease(
            f"s8-v4-x1-{suite_id}-training",
            source_commit=source["revision"],
            purpose="scale_validation_training",
            scenario_id="X1",
            model_family="heterogeneous",
            owner_pid=os.getpid(),
            ttl_seconds=7200,
        )
        assert_scale_validation_gpu_lease_owner(
            run_id=training_lease.run_id,
            lease_id=training_lease.lease_id,
            fencing_token=training_lease.fencing_token,
            purpose="scale_validation_training",
            scenario_id="X1",
            model_family="heterogeneous",
        )
        prepared = prepare_x1_artifacts(
            contract,
            output_root=artifact_root,
            source_revision=source["revision"],
            source_tree=source["tree_sha"],
            lease_run_id=training_lease.run_id,
            lease_id=training_lease.lease_id,
            fencing_token=training_lease.fencing_token,
        )
        artifact_manifest_path = Path(prepared["manifest_path"])
        artifact_manifest = validate_x1_artifacts(
            contract,
            manifest_path=artifact_manifest_path,
            source_revision=source["revision"],
            source_tree=source["tree_sha"],
            lease_run_id=training_lease.run_id,
            lease_id=training_lease.lease_id,
            fencing_token=training_lease.fencing_token,
        )
        released_training = release_lease(
            training_lease,
            reason=f"X1 suite {suite_id} artifact preparation complete",
        )
        canonical_write(suite_root / "training-lease-release.json", released_training)
        training_lease = None
        inference_lease = acquire_scale_validation_gpu_lease(
            f"s8-v4-x1-{suite_id}-inference",
            source_commit=source["revision"],
            purpose="scale_validation_inference",
            scenario_id="X1",
            model_family="heterogeneous",
            owner_pid=os.getpid(),
            ttl_seconds=28800,
        )
        write_prometheus_targets(suite_id)
        target_written = True

        active_profile = "disabled"
        runtime_manifest = write_runtime_manifest(
            contract,
            artifact_root=artifact_root,
            artifact_manifest_path=artifact_manifest_path,
            artifact_manifest=artifact_manifest,
            data_root=args.data_root,
            profile_id=active_profile,
            lease=inference_lease,
            source_revision=source["revision"],
        )
        profile_root = artifact_root / "model-repositories" / active_profile
        # kubectl apply may create a partial resource set before rollout readiness fails.
        topology_started = True
        apply_topology(
            contract,
            suite_root=suite_root,
            suite_id=suite_id,
            source_revision=source["revision"],
            api_image=api_image,
            api_replicas=1,
            cpu_workers=1,
            profile_relative_root=artifact_relative(profile_root, args.data_root),
            runtime_manifest_relative_path=artifact_relative(runtime_manifest, args.data_root),
            database_schema=database_schema,
            lease=inference_lease,
        )
        wait_runtime_ready()
        wait_x1_prometheus(present=True)
        q0 = run_q0(
            contract,
            suite_id=suite_id,
            artifact_root=artifact_root,
            artifact_manifest=artifact_manifest,
        )
        q0_path = suite_root / "q0.json"
        canonical_write(q0_path, q0)

        for cell in contract.solo_calibration_cells():
            _, projection = run_calibration_attempt(
                contract,
                suite_root=suite_root,
                suite_id=suite_id,
                cell_id=cell.cell_id,
                mode=cell.mode,
                model_id=cell.model_id,
                repetition=cell.repetition,
                api_replicas=1,
                cpu_workers=1,
                model_weights={str(cell.model_id): 1.0},
                artifact_manifest=artifact_manifest,
                artifact_root=artifact_root,
                lease=inference_lease,
            )
            solo_projections.append(projection)

        balanced_weights = _weights_from_solo(solo_projections)
        for replicas in API_REPLICAS:
            for workers in CPU_WORKERS:
                apply_topology(
                    contract,
                    suite_root=suite_root,
                    suite_id=suite_id,
                    source_revision=source["revision"],
                    api_image=api_image,
                    api_replicas=replicas,
                    cpu_workers=workers,
                    profile_relative_root=artifact_relative(profile_root, args.data_root),
                    runtime_manifest_relative_path=artifact_relative(
                        runtime_manifest, args.data_root
                    ),
                    database_schema=database_schema,
                    lease=inference_lease,
                )
                wait_runtime_ready()
                for repetition in REPETITIONS:
                    cell_id = f"topology_calibration-r{replicas}-w{workers}-rep{repetition}"
                    _, projection = run_calibration_attempt(
                        contract,
                        suite_root=suite_root,
                        suite_id=suite_id,
                        cell_id=cell_id,
                        mode="topology_calibration",
                        model_id=None,
                        repetition=repetition,
                        api_replicas=replicas,
                        cpu_workers=workers,
                        model_weights=balanced_weights,
                        artifact_manifest=artifact_manifest,
                        artifact_root=artifact_root,
                        lease=inference_lease,
                    )
                    topology_projections.append(projection)

        for profile_id in ("enabled-4-8-2ms", "enabled-8-16-10ms"):
            runtime_manifest = write_runtime_manifest(
                contract,
                artifact_root=artifact_root,
                artifact_manifest_path=artifact_manifest_path,
                artifact_manifest=artifact_manifest,
                data_root=args.data_root,
                profile_id=profile_id,
                lease=inference_lease,
                source_revision=source["revision"],
            )
            profile_root = artifact_root / "model-repositories" / profile_id
            apply_topology(
                contract,
                suite_root=suite_root,
                suite_id=suite_id,
                source_revision=source["revision"],
                api_image=api_image,
                api_replicas=1,
                cpu_workers=1,
                profile_relative_root=artifact_relative(profile_root, args.data_root),
                runtime_manifest_relative_path=artifact_relative(runtime_manifest, args.data_root),
                database_schema=database_schema,
                lease=inference_lease,
            )
            wait_runtime_ready()
            for model_id in MODEL_IDS:
                for repetition in REPETITIONS:
                    cell_id = f"batching_calibration-r1-w1-{model_id}-{profile_id}-rep{repetition}"
                    _, projection = run_calibration_attempt(
                        contract,
                        suite_root=suite_root,
                        suite_id=suite_id,
                        cell_id=cell_id,
                        mode="batching_calibration",
                        model_id=model_id,
                        repetition=repetition,
                        api_replicas=1,
                        cpu_workers=1,
                        model_weights={model_id: 1.0},
                        artifact_manifest=artifact_manifest,
                        artifact_root=artifact_root,
                        lease=inference_lease,
                        batch_candidate=profile_id,
                    )
                    batching_projections.append(projection)

        selected_batching = select_batching_profiles(batching_projections)
        load_freeze = compute_load_freeze(
            contract,
            model_calibrations=solo_projections,
            topology_calibrations=topology_projections,
        )
        load_freeze["selected_batching"] = selected_batching
        load_freeze["profiler_qualification"] = {
            "mode": "concurrent_balanced",
            "topology": "r1-w4",
            "repetitions": 3,
            "verdict": KERNEL_OVERLAP_FALLBACK,
            "reason": "No model/request-attributed Nsight or CUPTI kernel intervals were accepted during calibration.",
        }
        canonical_write(suite_root / "load-freeze.json", load_freeze)
    except Exception as exc:  # noqa: BLE001 - immutable fail-closed evidence
        failure = {
            "schema_version": "evm.s8_v4.x1_failed_attempt.v1",
            "suite_id": suite_id,
            "failed_at": utc_now(),
            "source_revision": source["revision"],
            "error_type": type(exc).__name__,
            "error": str(exc),
            "credit": "zero_credit",
            "acceptance_credit": False,
        }
        canonical_write(suite_root / "failed-attempt.json", failure)
    finally:
        actions: list[tuple[str, Callable[[], Any]]] = []
        if topology_started:
            actions.append(("kubernetes", delete_topology))
        if target_written:
            actions.append(("prometheus", remove_prometheus_targets))
        if database_schema_exists(database_schema):
            actions.append(("database_schema", lambda: drop_database_schema(database_schema)))
        if inference_lease is not None:
            actions.append(
                (
                    "inference_lease",
                    lambda: release_lease(
                        inference_lease,
                        reason=f"X1 suite {suite_id} cleanup",
                    ),
                )
            )
        if training_lease is not None:
            actions.append(
                (
                    "training_lease",
                    lambda: release_lease(
                        training_lease,
                        reason=f"X1 suite {suite_id} failed preparation cleanup",
                    ),
                )
            )
        if holder_scaled:
            actions.append(("b0", lambda: scale_holder(holder, holder.replicas)))
        actions.append(
            (
                "api_image",
                lambda: run(["docker", "image", "rm", api_image], check=False, timeout=120),
            )
        )
        for name, action in actions:
            try:
                action()
            except Exception as exc:  # noqa: BLE001 - preserve cleanup failure
                cleanup_errors.append(
                    {"action": name, "error_type": type(exc).__name__, "error": str(exc)}
                )
        if cleanup_errors:
            canonical_write(suite_root / "cleanup-errors.json", cleanup_errors)
    if not cleanup_errors:
        try:
            cleanup = cleanup_snapshot(
                holder=holder,
                gpu_before=gpu_before,
                prometheus_before=prometheus_before,
                database_schema=database_schema,
            )
            canonical_write(suite_root / "final-cleanup.json", cleanup)
        except Exception as exc:  # noqa: BLE001 - cleanup failure invalidates suite
            cleanup_errors.append(
                {
                    "action": "cleanup_validation",
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                }
            )
            canonical_write(suite_root / "cleanup-errors.json", cleanup_errors)
    index = private_index(suite_root)
    index_path = suite_root / "private-evidence-index.json"
    canonical_write(index_path, index)
    index["index_sha256"] = sha256_file(index_path)
    if failure is not None or cleanup_errors:
        print(
            json.dumps(
                {
                    "suite_id": suite_id,
                    "status": "zero_credit",
                    "failure": failure,
                    "cleanup_errors": cleanup_errors,
                    "private_index_sha256": index["index_sha256"],
                },
                allow_nan=False,
                sort_keys=True,
            )
        )
        return 1
    if artifact_manifest_path is None or q0_path is None or cleanup is None:
        raise X1ExperimentError("x1_completion_artifact_missing")
    public = _public_projection(
        suite_id=suite_id,
        source=source,
        contract=contract,
        artifact_manifest_path=artifact_manifest_path,
        q0_path=q0_path,
        solo=solo_projections,
        topology=topology_projections,
        batching=batching_projections,
        selected_batching=selected_batching,
        load_freeze=load_freeze,
        cleanup=cleanup,
        index=index,
    )
    canonical_write(args.public_output, public)
    print(json.dumps(public, allow_nan=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
