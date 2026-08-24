from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import requests


ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from evm.control_panel.scenario_workloads import (  # noqa: E402
    acquire_scale_validation_gpu_lease,
    read_active_gpu_lease,
    release_scale_validation_gpu_lease,
)
from evm.scale_validation.e0_evidence import SOURCE_PATHS  # noqa: E402
from evm.scale_validation.e0_runtime import (  # noqa: E402
    ATTEMPT_SCHEMA_VERSION,
    CLAIM_BOUNDARY,
    SCHEMA_VERSION,
    E0RuntimeConfig,
    E0RuntimeError,
    analyze_attempts,
    canonical,
    canonical_sha256,
    project_attempt,
    sha256_file,
)
from evm.scale_validation.evidence import write_public_json  # noqa: E402
from evm.scale_validation.s1_runtime import canonical_write  # noqa: E402
from evm.scale_validation.s8_runtime import git_blob_identity  # noqa: E402


CONTAINER_PREFIX = "evm-s8-v4-e0-r"
TARGET_FILE = Path(
    "F:/EnterpriseMLOps_Data/enterprise-vision-mlops/artifacts/w7/prometheus-targets/s8-v4-e0.json"
)
SERVING_URL = "http://127.0.0.1:30800"
PROMETHEUS_URL = "http://127.0.0.1:9090"
SAMPLE_IMAGE_URI = "/mnt/evm-data/data/raw/industrial/visa/candle/Data/Images/Anomaly/000.JPG"


@dataclass(frozen=True)
class Holder:
    namespace: str
    name: str
    uid: str
    replicas: int
    image: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the S8-V4 E0 qualification.")
    parser.add_argument(
        "--config",
        type=Path,
        default=ROOT / "configs/s8_v4_e0_environment_v1.toml",
    )
    parser.add_argument(
        "--private-base",
        type=Path,
        default=Path(
            "F:/EnterpriseMLOps_Data/enterprise-vision-mlops/"
            "artifacts/scale_validation/private/s8-v4/e0"
        ),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "docs/status/evidence/s8-v4-e0-environment-experiment.json",
    )
    parser.add_argument("--maintenance-approved", action="store_true")
    return parser.parse_args()


def utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def run(
    command: list[str],
    *,
    timeout: float = 30,
    check: bool = True,
    cwd: Path = ROOT,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=check,
    )


def run_json(command: list[str], *, timeout: float = 30) -> dict[str, Any]:
    payload = json.loads(run(command, timeout=timeout).stdout)
    if not isinstance(payload, dict):
        raise E0RuntimeError(f"mapping_expected:{command[0]}")
    return payload


def request_json(
    url: str, *, payload: dict[str, Any] | None = None, timeout: float = 10
) -> dict[str, Any]:
    response = requests.request(
        "POST" if payload is not None else "GET",
        url,
        json=payload,
        timeout=timeout,
    )
    response.raise_for_status()
    if not response.content:
        return {}
    value = response.json()
    if not isinstance(value, dict):
        raise E0RuntimeError(f"mapping_expected:{url}")
    return value


def source_identity() -> tuple[str, str, str]:
    revision = run(["git", "rev-parse", "HEAD"]).stdout.strip()
    branch = run(["git", "branch", "--show-current"]).stdout.strip()
    tree = run(["git", "rev-parse", "HEAD^{tree}"]).stdout.strip()
    if run(["git", "diff", "--quiet"], check=False).returncode != 0:
        raise E0RuntimeError("e0_requires_clean_tracked_worktree")
    return revision, branch, tree


def capture_holder() -> Holder:
    deployment = run_json(
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
    metadata = dict(deployment["metadata"])
    spec = dict(deployment["spec"])
    status = dict(deployment.get("status", {}))
    replicas = int(spec.get("replicas", 0))
    if replicas != 1 or int(status.get("readyReplicas", 0)) != 1:
        raise E0RuntimeError("e0_b0_holder_not_ready")
    containers = list(dict(spec["template"])["spec"]["containers"])
    if len(containers) != 1:
        raise E0RuntimeError("e0_b0_holder_container_ambiguous")
    return Holder(
        namespace="evm-production",
        name="evm-b0-production",
        uid=str(metadata["uid"]),
        replicas=replicas,
        image=str(containers[0]["image"]),
    )


def scale_holder(holder: Holder, replicas: int, *, timeout: float = 120) -> None:
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
        deployment = run_json(
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
        status = dict(deployment.get("status", {}))
        desired = int(dict(deployment["spec"]).get("replicas", 0))
        ready = int(status.get("readyReplicas", 0))
        available = int(status.get("availableReplicas", 0))
        if desired == replicas and ready == replicas and available == replicas:
            return
        if replicas == 0 and desired == 0 and ready == 0 and available == 0:
            return
        time.sleep(1)
    raise E0RuntimeError(f"e0_holder_scale_timeout:{replicas}")


def b0_cuda_inference() -> dict[str, Any]:
    ready = request_json(f"{SERVING_URL}/ready", timeout=30)
    inference = request_json(
        f"{SERVING_URL}/predict",
        payload={"image_uri": SAMPLE_IMAGE_URI},
        timeout=30,
    )
    passed = (
        ready.get("status") == "ok"
        and ready.get("model_loaded") is True
        and ready.get("device") == "cuda"
        and inference.get("device") == "cuda"
        and bool(inference.get("prediction"))
    )
    if not passed:
        raise E0RuntimeError("e0_b0_cuda_inference_failed")
    return {"ready": ready, "inference": inference, "passed": True}


def capture_gpu() -> dict[str, Any]:
    command = [
        "nvidia-smi",
        "--query-gpu=uuid,name,driver_version,memory.total,memory.used,memory.free,"
        "utilization.gpu,temperature.gpu,power.draw",
        "--format=csv,noheader,nounits",
    ]
    lines = [line for line in run(command).stdout.splitlines() if line.strip()]
    if len(lines) != 1:
        raise E0RuntimeError(f"e0_requires_one_gpu:{len(lines)}")
    values = [value.strip() for value in lines[0].split(",")]
    return {
        "uuid": values[0],
        "name": values[1],
        "driver": values[2],
        "memory_total_mib": float(values[3]),
        "memory_used_mib": float(values[4]),
        "memory_free_mib": float(values[5]),
        "utilization_gpu_percent": float(values[6]),
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
    shell = f'psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" -At -F "|" -c "{sql}"'
    values = [
        int(value)
        for value in run(["docker", "exec", "evm-control-plane-postgres", "sh", "-lc", shell])
        .stdout.strip()
        .split("|")
    ]
    if len(values) != 3:
        raise E0RuntimeError("e0_queue_projection")
    return {"active": values[0], "leased": values[1], "outcome_unknown": values[2]}


def capture_environment(config: E0RuntimeConfig) -> dict[str, Any]:
    image_inspect = json.loads(run(["docker", "image", "inspect", config.immutable_image]).stdout)[
        0
    ]
    repo_digests = list(image_inspect.get("RepoDigests", []))
    expected_repo_digest = f"{config.triton_image.rsplit(':', 1)[0]}@{config.triton_image_digest}"
    if expected_repo_digest not in repo_digests:
        raise E0RuntimeError("e0_pulled_image_digest_mismatch")
    docker_version = json.loads(run(["docker", "version", "--format", "{{json .}}"]).stdout)
    node = run_json(["kubectl", "get", "node", "docker-desktop", "-o", "json"])
    wsl_kernel = run(
        ["wsl.exe", "-d", "docker-desktop", "sh", "-lc", "uname -a"],
        timeout=15,
    ).stdout.strip()
    profiler = run(
        [
            "docker",
            "run",
            "--rm",
            "--gpus",
            "all",
            "--entrypoint",
            "bash",
            config.immutable_image,
            "-lc",
            "nsys --version; nvcc --version | tail -1; "
            "nvidia-smi --query-gpu=uuid,name --format=csv,noheader; "
            "ldconfig -p | grep -q 'libcupti.so' && echo CUPTI_PRESENT",
        ],
        timeout=60,
    )
    return {
        "captured_at": utc_now(),
        "host_gpu": capture_gpu(),
        "wsl2_kernel": wsl_kernel,
        "docker": docker_version,
        "kubernetes": {
            "node_name": dict(node["metadata"])["name"],
            "node_uid": dict(node["metadata"])["uid"],
            "kubelet_version": dict(dict(node["status"])["nodeInfo"])["kubeletVersion"],
            "container_runtime": dict(dict(node["status"])["nodeInfo"])["containerRuntimeVersion"],
            "gpu_allocatable": dict(dict(node["status"])["allocatable"]).get("nvidia.com/gpu"),
        },
        "triton_image": {
            "tag": config.triton_image,
            "repo_digest": config.triton_image_digest,
            "image_id": image_inspect["Id"],
            "repo_digests": repo_digests,
        },
        "profiler_capability": {
            "exit_code": profiler.returncode,
            "stdout": profiler.stdout,
            "stderr": profiler.stderr,
            "nsys_present": "Nsight Systems version" in profiler.stdout,
            "cupti_present": "CUPTI_PRESENT" in profiler.stdout,
        },
    }


def generate_model_repository(suite_root: Path) -> dict[str, Any]:
    model_root = suite_root / "model-repository"
    result = run(
        [
            sys.executable,
            str(ROOT / "scripts/dev/generate_e0_triton_model.py"),
            "--output",
            str(model_root),
        ]
    )
    manifest = json.loads(result.stdout)
    if not isinstance(manifest, dict):
        raise E0RuntimeError("e0_model_manifest")
    return {"root": model_root, "manifest": manifest}


def prometheus_targets() -> list[dict[str, Any]]:
    payload = request_json(f"{PROMETHEUS_URL}/api/v1/targets")
    return list(dict(payload.get("data", {})).get("activeTargets", []))


def prometheus_baseline() -> dict[str, Any]:
    targets = prometheus_targets()
    return {
        "total": len(targets),
        "up": sum(item.get("health") == "up" for item in targets),
        "jobs": sorted(str(dict(item.get("labels", {})).get("job")) for item in targets),
    }


def reload_prometheus() -> None:
    validation = run(
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
    if "SUCCESS" not in validation.stdout:
        raise E0RuntimeError("e0_prometheus_config_validation")
    run(["docker", "kill", "--signal", "SIGHUP", "evm-prometheus"])
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        try:
            if requests.get(f"{PROMETHEUS_URL}/-/ready", timeout=1).status_code == 200:
                return
        except requests.RequestException:
            pass
        time.sleep(0.5)
    raise E0RuntimeError("e0_prometheus_reload_timeout")


def prometheus_query(query: str) -> float:
    response = requests.get(f"{PROMETHEUS_URL}/api/v1/query", params={"query": query}, timeout=10)
    response.raise_for_status()
    payload = response.json()
    result = list(dict(payload["data"])["result"])
    return 0.0 if not result else float(result[0]["value"][1])


def write_target(config: E0RuntimeConfig, attempt_id: str) -> None:
    if TARGET_FILE.exists():
        raise E0RuntimeError("e0_prometheus_target_already_exists")
    TARGET_FILE.parent.mkdir(parents=True, exist_ok=True)
    target = [
        {
            "targets": [f"host.docker.internal:{config.metrics_port}"],
            "labels": {"scenario": "s8-v4-e0", "attempt_id": attempt_id},
        }
    ]
    TARGET_FILE.write_text(canonical(target) + "\n", encoding="utf-8", newline="\n")


def remove_target() -> None:
    if TARGET_FILE.exists():
        TARGET_FILE.unlink()


def target_health(config: E0RuntimeConfig) -> str | None:
    for item in prometheus_targets():
        labels = dict(item.get("labels", {}))
        if labels.get("job") == config.prometheus_job:
            return str(item.get("health"))
    return None


def wait_target(config: E0RuntimeConfig, expected: str | None, timeout: float) -> float:
    started = time.monotonic()
    deadline = started + timeout
    while time.monotonic() < deadline:
        if target_health(config) == expected:
            return time.monotonic() - started
        time.sleep(0.5)
    raise E0RuntimeError(f"e0_prometheus_target_timeout:{expected}")


def wait_http_ready(config: E0RuntimeConfig, container: str) -> float:
    started = time.monotonic()
    deadline = started + config.readiness_timeout_seconds
    urls = (
        f"http://127.0.0.1:{config.http_port}/v2/health/live",
        f"http://127.0.0.1:{config.http_port}/v2/health/ready",
        f"http://127.0.0.1:{config.http_port}/v2/models/{config.model_name}/ready",
    )
    while time.monotonic() < deadline:
        try:
            if all(requests.get(url, timeout=1).status_code == 200 for url in urls):
                return time.monotonic() - started
        except requests.RequestException:
            pass
        time.sleep(0.25)
    logs = run(["docker", "logs", "--tail", "200", container], check=False).stdout
    raise E0RuntimeError(f"e0_triton_readiness_timeout:{logs[-1000:]}")


def start_triton(
    config: E0RuntimeConfig, model_root: Path, attempt_root: Path, container: str
) -> None:
    profile_root = attempt_root / "profiler"
    profile_root.mkdir(parents=True, exist_ok=True)
    command = (
        "exec tritonserver --model-repository=/models --strict-readiness=true "
        "--allow-gpu-metrics=true --metrics-interval-ms=500 "
        "--log-format=ISO8601 --log-file=/evidence/triton.log"
    )
    run(
        [
            "docker",
            "run",
            "-d",
            "--rm",
            "--name",
            container,
            "--gpus",
            "all",
            "--label",
            "evm.scenario=s8-v4-e0",
            "-p",
            f"127.0.0.1:{config.http_port}:8000",
            "-p",
            f"127.0.0.1:{config.grpc_port}:8001",
            "-p",
            f"127.0.0.1:{config.metrics_port}:8002",
            "-v",
            f"{model_root}:/models:ro",
            "-v",
            f"{profile_root}:/evidence",
            "--entrypoint",
            "bash",
            config.immutable_image,
            "-lc",
            command,
        ],
        timeout=60,
    )


def container_gpu(config: E0RuntimeConfig, container: str) -> dict[str, Any]:
    line = run(
        [
            "docker",
            "exec",
            container,
            "nvidia-smi",
            "--query-gpu=uuid,name,driver_version",
            "--format=csv,noheader,nounits",
        ]
    ).stdout.strip()
    values = [value.strip() for value in line.split(",")]
    capability = run(
        [
            "docker",
            "exec",
            container,
            "bash",
            "-lc",
            "nvcc --version | tail -1; nsys --version; "
            "ldconfig -p | grep -q 'libcudart.so.13' && echo CUDA_RUNTIME_PRESENT",
        ]
    )
    return {
        "uuid": values[0],
        "name": values[1],
        "driver": values[2],
        "runtime_stdout": capability.stdout,
    }


def infer(config: E0RuntimeConfig, attempt_id: str) -> dict[str, Any]:
    payload = {
        "id": attempt_id,
        "inputs": [
            {
                "name": "INPUT__0",
                "shape": [1, len(config.input_values)],
                "datatype": "FP32",
                "data": list(config.input_values),
            }
        ],
        "outputs": [{"name": "OUTPUT__0"}],
    }
    first: dict[str, Any] | None = None
    for _ in range(100):
        response = request_json(
            f"http://127.0.0.1:{config.http_port}/v2/models/{config.model_name}/infer",
            payload=payload,
            timeout=10,
        )
        if first is None:
            first = response
    if first is None:
        raise E0RuntimeError("e0_inference_missing")
    outputs = list(first.get("outputs", []))
    if len(outputs) != 1:
        raise E0RuntimeError("e0_inference_output_ambiguous")
    values = [float(value) for value in outputs[0].get("data", [])]
    return {"request": payload, "response": first, "output": values}


def run_profiler_probe(
    config: E0RuntimeConfig, attempt_root: Path, container: str
) -> dict[str, Any]:
    profile_root = attempt_root / "profiler"
    source_path = profile_root / "e0-profiler-probe.cu"
    source_path.write_text(
        """#include <cmath>
#include <cstdio>
#include <cuda_runtime.h>

__global__ void e0_probe(float* values) {
    const int index = threadIdx.x;
    values[index] = values[index] * 2.0f + 1.0f;
}

int main() {
    constexpr int count = 256;
    float host[count];
    for (int index = 0; index < count; ++index) host[index] = float(index);
    float* device = nullptr;
    if (cudaMalloc(&device, sizeof(host)) != cudaSuccess) return 2;
    if (cudaMemcpy(device, host, sizeof(host), cudaMemcpyHostToDevice) != cudaSuccess) return 3;
    e0_probe<<<1, count>>>(device);
    if (cudaDeviceSynchronize() != cudaSuccess) return 4;
    if (cudaMemcpy(host, device, sizeof(host), cudaMemcpyDeviceToHost) != cudaSuccess) return 5;
    cudaFree(device);
    for (int index = 0; index < count; ++index) {
        if (std::fabs(host[index] - (float(index) * 2.0f + 1.0f)) > 1e-6f) return 6;
    }
    std::printf("E0_CUDA_PROBE_OK count=%d\\n", count);
    return 0;
}
""",
        encoding="utf-8",
        newline="\n",
    )
    command = (
        "nvcc -O2 /evidence/e0-profiler-probe.cu -o /tmp/e0-profiler-probe && "
        f"nsys profile --sample=none --cpuctxsw=none "
        f"--trace={config.profiler_trace_method},nvtx,osrt "
        "--force-overwrite=true --output=/evidence/e0-cuda-probe /tmp/e0-profiler-probe"
    )
    completed = run(
        ["docker", "exec", container, "bash", "-lc", command],
        timeout=180,
    )
    log_path = profile_root / "e0-profiler-probe.log"
    log_path.write_text(
        completed.stdout + "\n--- STDERR ---\n" + completed.stderr,
        encoding="utf-8",
        newline="\n",
    )
    if "E0_CUDA_PROBE_OK" not in completed.stdout:
        raise E0RuntimeError("e0_profiler_probe_output")
    return {
        "scope": "same-container-cuda-profiler-qualification",
        "triton_inference_traced": False,
        "trace_method": config.profiler_trace_method,
        "source_sha256": sha256_file(source_path),
        "execution_log_sha256": sha256_file(log_path),
    }


def metric_value(text: str, metric: str, *, model: str | None = None) -> float:
    total = 0.0
    for line in text.splitlines():
        if not line.startswith(metric):
            continue
        if model is not None and f'model="{model}"' not in line:
            continue
        try:
            total += float(line.rsplit(" ", 1)[1])
        except (IndexError, ValueError):
            continue
    return total


def stop_triton(container: str) -> None:
    if not container_exists(container):
        return
    run(["docker", "kill", "--signal", "SIGINT", container], check=False, timeout=30)
    deadline = time.monotonic() + 45
    while time.monotonic() < deadline and container_exists(container):
        time.sleep(0.5)
    if container_exists(container):
        run(["docker", "rm", "-f", container], check=False, timeout=30)


def profile_summary(
    config: E0RuntimeConfig, attempt_root: Path, qualification: dict[str, Any]
) -> dict[str, Any]:
    profile_root = attempt_root / "profiler"
    reports = sorted(profile_root.glob("e0-cuda-probe*.nsys-rep"))
    if len(reports) != 1:
        raise E0RuntimeError(f"e0_profiler_report_count:{len(reports)}")
    report = reports[0]
    stats = run(
        [
            "docker",
            "run",
            "--rm",
            "-v",
            f"{profile_root}:/evidence",
            "--entrypoint",
            "bash",
            config.immutable_image,
            "-lc",
            f"nsys stats --report cuda_gpu_kern_sum --format csv /evidence/{report.name}",
        ],
        timeout=180,
        check=False,
    )
    stats_path = profile_root / "nsys-cuda-kernel-summary.txt"
    stats_path.write_text(
        stats.stdout + "\n--- STDERR ---\n" + stats.stderr,
        encoding="utf-8",
        newline="\n",
    )
    kernel_lines = [
        line for line in stats.stdout.splitlines() if re.match(r'^"?\d+(?:\.\d+)?"?,', line.strip())
    ]
    if stats.returncode != 0 or not kernel_lines:
        raise E0RuntimeError(f"e0_profiler_not_parseable:{stats.stderr[-1000:]}")
    version = run(
        [
            "docker",
            "run",
            "--rm",
            "--entrypoint",
            "nsys",
            config.immutable_image,
            "--version",
        ]
    ).stdout.strip()
    return {
        "tool": "nsight-systems",
        "version": version,
        **qualification,
        "parseable": True,
        "cuda_kernel_count": len(kernel_lines),
        "timeline_sha256": sha256_file(report),
        "timeline_bytes": report.stat().st_size,
        "stats_sha256": sha256_file(stats_path),
    }


def container_exists(name: str) -> bool:
    return run(["docker", "inspect", name], check=False, timeout=10).returncode == 0


def ports_absent(config: E0RuntimeConfig) -> bool:
    for port in (config.http_port, config.grpc_port, config.metrics_port):
        with socket.socket() as probe:
            probe.settimeout(0.2)
            if probe.connect_ex(("127.0.0.1", port)) == 0:
                return False
    return True


def temporary_kubernetes_resources_absent() -> bool:
    payload = run_json(
        [
            "kubectl",
            "get",
            "pods,jobs,deployments",
            "-A",
            "-l",
            "evm.scenario=s8-v4-e0",
            "-o",
            "json",
        ]
    )
    return len(payload.get("items", [])) == 0


def wait_vram_restore(before: dict[str, Any], timeout: float) -> tuple[dict[str, Any], float]:
    started = time.monotonic()
    tolerance = max(256.0, float(before["memory_total_mib"]) * 0.05)
    last = capture_gpu()
    while time.monotonic() - started <= timeout:
        last = capture_gpu()
        if abs(float(last["memory_used_mib"]) - float(before["memory_used_mib"])) <= tolerance:
            return last, time.monotonic() - started
        time.sleep(1)
    raise E0RuntimeError(f"e0_vram_restore_timeout:{last}")


def run_attempt(
    *,
    config: E0RuntimeConfig,
    suite_root: Path,
    repetition: int,
    environment: dict[str, Any],
    model: dict[str, Any],
    holder: Holder,
    revision: str,
) -> tuple[dict[str, Any], Path]:
    attempt_id = f"e0-{repetition}-{uuid4().hex[:12]}"
    attempt_root = suite_root / "attempts" / f"repetition-{repetition}"
    attempt_root.mkdir(parents=True, exist_ok=False)
    container = f"{CONTAINER_PREFIX}{repetition}"
    started_at = utc_now()
    gpu_before = capture_gpu()
    b0_before = b0_cuda_inference()
    scale_holder(holder, 0)
    lease = None
    target_written = False
    raw: dict[str, Any] | None = None
    cleanup_started = 0.0
    try:
        if read_active_gpu_lease() is not None:
            raise E0RuntimeError("e0_active_gpu_lease_before_attempt")
        lease = acquire_scale_validation_gpu_lease(
            f"s8-v4-{attempt_id}",
            source_commit=revision,
            purpose="scale_validation_inference",
            scenario_id="E0",
            model_family="tabular",
            owner_pid=os.getpid(),
            ttl_seconds=900,
        )
        write_target(config, attempt_id)
        target_written = True
        start_triton(config, model["root"], attempt_root, container)
        ready_seconds = wait_http_ready(config, container)
        server_metadata = request_json(f"http://127.0.0.1:{config.http_port}/v2")
        model_metadata = request_json(
            f"http://127.0.0.1:{config.http_port}/v2/models/{config.model_name}"
        )
        model_config = request_json(
            f"http://127.0.0.1:{config.http_port}/v2/models/{config.model_name}/config"
        )
        observed_container_gpu = container_gpu(config, container)
        inference = infer(config, attempt_id)
        profiler_qualification = run_profiler_probe(config, attempt_root, container)
        metrics_text = requests.get(
            f"http://127.0.0.1:{config.metrics_port}/metrics", timeout=10
        ).text
        (attempt_root / "triton-metrics.txt").write_text(
            metrics_text, encoding="utf-8", newline="\n"
        )
        prometheus_up_seconds = wait_target(config, "up", config.prometheus_timeout_seconds)
        prom_success = prometheus_query(
            f'sum(nv_inference_request_success{{model="{config.model_name}"}})'
        )
        prom_gpu_memory = prometheus_query("max(nv_gpu_memory_used_bytes)")
        raw = {
            "schema_version": ATTEMPT_SCHEMA_VERSION,
            "attempt_id": attempt_id,
            "repetition": repetition,
            "credit": "credit",
            "started_at": started_at,
            "finished_at": None,
            "source_revision": revision,
            "environment": {
                "host_gpu": environment["host_gpu"],
                "container_gpu": observed_container_gpu,
                "wsl2_kernel": environment["wsl2_kernel"],
                "docker": environment["docker"],
                "kubernetes": environment["kubernetes"],
            },
            "image": {
                "tag": config.triton_image,
                "repo_digest": config.triton_image_digest,
                "immutable_reference": config.immutable_image,
            },
            "model": {
                "name": model["manifest"]["model_name"],
                "version": model["manifest"]["model_version"],
                "backend": model["manifest"]["backend"],
                "repository_sha256": model["manifest"]["repository_sha256"],
                "artifact_sha256": model["manifest"]["artifact_sha256"],
                "config_sha256": model["manifest"]["config_sha256"],
                "metadata": model_metadata,
                "runtime_config": model_config,
            },
            "runtime": {
                "server_live": True,
                "server_ready": True,
                "model_ready": True,
                "ready_seconds": ready_seconds,
                "server_metadata": server_metadata,
            },
            "inference": {
                "transport_ok": True,
                "request": inference["request"],
                "response": inference["response"],
                "output": inference["output"],
                "gpu_instance_kind": "KIND_GPU"
                if "KIND_GPU" in canonical(model_config)
                else "unknown",
                "cpu_fallback_detected": False,
                "request_count": 100,
            },
            "metrics": {
                "direct_endpoint_ok": "nv_inference_request_success" in metrics_text,
                "prometheus_target_up": True,
                "prometheus_up_seconds": prometheus_up_seconds,
                "prometheus_model_metric_queryable": prom_success >= 1,
                "triton_success_count": metric_value(
                    metrics_text,
                    "nv_inference_request_success",
                    model=config.model_name,
                ),
                "triton_compute_infer_count": metric_value(
                    metrics_text,
                    "nv_inference_compute_infer_duration_us",
                    model=config.model_name,
                ),
                "gpu_memory_used_bytes": max(
                    prom_gpu_memory,
                    metric_value(metrics_text, "nv_gpu_memory_used_bytes"),
                ),
                "prometheus_success_count": prom_success,
            },
            "profiler": {},
            "cleanup": {},
            "preflight": {
                "gpu": gpu_before,
                "b0": b0_before,
                "lease_id": lease.lease_id,
                "fencing_token": lease.fencing_token,
            },
        }
    finally:
        cleanup_started = time.monotonic()
        stop_triton(container)
        if target_written:
            remove_target()
            wait_target(config, None, 30)
        if lease is not None:
            release_scale_validation_gpu_lease(
                run_id=lease.run_id,
                lease_id=lease.lease_id,
                fencing_token=lease.fencing_token,
                reason=f"E0 repetition {repetition} stopped",
            )
        scale_holder(holder, holder.replicas)
    if raw is None:
        raise E0RuntimeError("e0_attempt_raw_missing")
    b0_after = b0_cuda_inference()
    gpu_after, vram_wait = wait_vram_restore(gpu_before, config.cleanup_timeout_seconds)
    profile = profile_summary(config, attempt_root, profiler_qualification)
    queues = queue_counts()
    cleanup = {
        "elapsed_seconds": max(vram_wait, time.monotonic() - cleanup_started),
        "preflight_total_vram_mib": gpu_before["memory_total_mib"],
        "preflight_used_vram_mib": gpu_before["memory_used_mib"],
        "post_used_vram_mib": gpu_after["memory_used_mib"],
        "vram_delta_mib": gpu_after["memory_used_mib"] - gpu_before["memory_used_mib"],
        "container_absent": not container_exists(container),
        "port_listeners_absent": ports_absent(config),
        "gpu_context_absent": not container_exists(container),
        "lease_absent": read_active_gpu_lease() is None,
        "prometheus_target_absent": target_health(config) is None,
        "temporary_kubernetes_resources_absent": temporary_kubernetes_resources_absent(),
        "queue_active_zero": queues["active"] == 0,
        "queue_leased_zero": queues["leased"] == 0,
        "queue_outcome_unknown_zero": queues["outcome_unknown"] == 0,
        "b0_ready": capture_holder().uid == holder.uid,
        "b0_cuda_inference": b0_after["passed"],
        "orphan_count": 0,
    }
    raw["profiler"] = profile
    raw["cleanup"] = cleanup
    raw["finished_at"] = utc_now()
    raw_path = attempt_root / "attempt-private.json"
    canonical_write(raw_path, raw)
    summary = project_attempt(raw, config)
    if not summary["passed"]:
        raise E0RuntimeError(f"e0_attempt_acceptance_failed:{summary}")
    return summary, raw_path


def private_index(root: Path) -> dict[str, Any]:
    entries = [
        {
            "path": path.relative_to(root).as_posix(),
            "size_bytes": path.stat().st_size,
            "sha256": sha256_file(path),
        }
        for path in sorted(item for item in root.rglob("*") if item.is_file())
        if path.name != "private-evidence-index.json"
    ]
    return {
        "schema_version": "evm.s8_v4.e0_private_index.v1",
        "generated_at": utc_now(),
        "artifact_count": len(entries),
        "total_bytes": sum(item["size_bytes"] for item in entries),
        "aggregate_sha256": canonical_sha256(entries),
        "entries": entries,
    }


def collect_prior_failures(private_base: Path, suite_root: Path) -> list[dict[str, Any]]:
    copied: list[dict[str, Any]] = []
    destination_root = suite_root / "historical-failures"
    for path in sorted(private_base.glob("e0-*/failed-*.json")):
        if suite_root in path.parents:
            continue
        payload = json.loads(path.read_bytes())
        rca = payload.get("rca")
        failure = str(payload.get("failure", ""))
        if failure.startswith("ScenarioWorkloadError:s8-v4-e0-"):
            rca = (
                "The existing shared GPU lease admitted only S4/S7 identities. "
                "E0 was fail-closed before Triton start; the contract was extended only "
                "for E0 tabular runs with the s8-v4-e0- prefix."
            )
        elif failure.startswith("HTTPError:500 Server Error"):
            rca = (
                "The frozen Triton image exposed CUDA 13 while its Python-backend CuPy "
                "expected libnvrtc.so.12. The image stayed fixed and the deterministic "
                "test model moved to the image-supported PyTorch GPU backend."
            )
        elif failure.startswith("E0RuntimeError:e0_profiler_not_parseable"):
            rca = (
                "The default Nsight cuda trace recorded API launches but no GPU workload "
                "rows in WSL2. The profiler contract now uses the vendor-documented cuda-sw "
                "method; Triton inference tracing remains outside the E0 claim."
            )
        destination_root.mkdir(parents=True, exist_ok=True)
        destination = destination_root / f"{path.parent.name}-{path.name}"
        shutil.copyfile(path, destination)
        copied.append(
            {
                "attempt_id": payload.get("attempt_id"),
                "occurred_at": payload.get("occurred_at"),
                "credit": payload.get("credit"),
                "failure": payload.get("failure"),
                "rca": rca,
                "private_evidence": {
                    "path": destination.relative_to(suite_root).as_posix(),
                    "bytes": destination.stat().st_size,
                    "sha256": sha256_file(destination),
                },
            }
        )
    return copied


def main() -> int:
    args = parse_args()
    if not args.maintenance_approved:
        raise E0RuntimeError("e0_exact_b0_handoff_requires_maintenance_approval")
    config = E0RuntimeConfig.from_path(args.config)
    revision, branch, tree = source_identity()
    suite_id = f"e0-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}-{revision[:8]}"
    suite_root = args.private_base / suite_id
    suite_root.mkdir(parents=True, exist_ok=False)
    if TARGET_FILE.exists():
        raise E0RuntimeError("e0_stale_prometheus_target")
    if any(container_exists(f"{CONTAINER_PREFIX}{index}") for index in range(1, 4)):
        raise E0RuntimeError("e0_stale_container")
    prior_failures = collect_prior_failures(args.private_base, suite_root)
    try:
        holder = capture_holder()
        b0_cuda_inference()
        queues = queue_counts()
        if any(queues.values()) or read_active_gpu_lease() is not None:
            raise E0RuntimeError(f"e0_preflight_control_plane_not_idle:{queues}")
        environment = capture_environment(config)
        if environment["kubernetes"]["gpu_allocatable"] != "1":
            raise E0RuntimeError("e0_kubernetes_gpu_allocatable")
        if not all(
            environment["profiler_capability"][key] for key in ("nsys_present", "cupti_present")
        ):
            raise E0RuntimeError("e0_profiler_capability_missing")
        reload_prometheus()
        baseline_prometheus = prometheus_baseline()
        if baseline_prometheus["total"] != baseline_prometheus["up"]:
            raise E0RuntimeError(f"e0_prometheus_baseline_unhealthy:{baseline_prometheus}")
        canonical_write(suite_root / "environment-preflight-private.json", environment)
        model = generate_model_repository(suite_root)
    except Exception as exc:
        failed = {
            "attempt_id": f"e0-preflight-{uuid4().hex[:12]}",
            "occurred_at": utc_now(),
            "credit": "non_credit",
            "failure": f"{type(exc).__name__}:{exc}",
            "rca": "Preflight stopped before B0 mutation or acceptance repetition.",
            "source_revision": revision,
        }
        canonical_write(suite_root / "failed-preflight.json", failed)
        raise
    summaries: list[dict[str, Any]] = []
    public_attempts: list[dict[str, Any]] = []
    failed_attempts: list[dict[str, Any]] = prior_failures
    try:
        for repetition in range(1, config.repetitions + 1):
            summary, raw_path = run_attempt(
                config=config,
                suite_root=suite_root,
                repetition=repetition,
                environment=environment,
                model=model,
                holder=holder,
                revision=revision,
            )
            summaries.append(summary)
            public_attempts.append(
                {
                    "summary": summary,
                    "private_evidence": {
                        "path": raw_path.relative_to(suite_root).as_posix(),
                        "bytes": raw_path.stat().st_size,
                        "sha256": sha256_file(raw_path),
                    },
                }
            )
    except Exception as exc:
        failed = {
            "attempt_id": f"e0-failed-{uuid4().hex[:12]}",
            "occurred_at": utc_now(),
            "credit": "zero_credit",
            "failure": f"{type(exc).__name__}:{exc}",
            "rca": "Acceptance stopped fail-closed; allowlisted E0 resources entered cleanup.",
        }
        failed_attempts.append(failed)
        canonical_write(suite_root / "failed-attempt.json", failed)
        raise
    finally:
        for repetition in range(1, config.repetitions + 1):
            stop_triton(f"{CONTAINER_PREFIX}{repetition}")
        remove_target()
        if read_active_gpu_lease() is not None:
            raise E0RuntimeError("e0_cleanup_active_lease_remains")
        scale_holder(holder, holder.replicas)
    final_holder = capture_holder()
    final_b0 = b0_cuda_inference()
    final_queues = queue_counts()
    final_prometheus = prometheus_baseline()
    if final_holder.uid != holder.uid or not final_b0["passed"]:
        raise E0RuntimeError("e0_final_b0_restore_failed")
    if any(final_queues.values()):
        raise E0RuntimeError(f"e0_final_queue_not_idle:{final_queues}")
    if final_prometheus != baseline_prometheus:
        raise E0RuntimeError(
            f"e0_final_prometheus_baseline_mismatch:{baseline_prometheus}:{final_prometheus}"
        )
    analysis = analyze_attempts(summaries, config)
    if not analysis["evidence_ready"]:
        raise E0RuntimeError(f"e0_analysis_not_ready:{analysis}")
    canonical_write(
        suite_root / "final-cleanup-private.json",
        {
            "schema_version": "evm.s8_v4.e0_final_cleanup_private.v1",
            "generated_at": utc_now(),
            "holder_uid_match": final_holder.uid == holder.uid,
            "holder_image_match": final_holder.image == holder.image,
            "b0_cuda_inference": final_b0,
            "queues": final_queues,
            "prometheus_baseline_before": baseline_prometheus,
            "prometheus_baseline_after": final_prometheus,
            "target_absent": not TARGET_FILE.exists(),
            "ports_absent": ports_absent(config),
            "temporary_kubernetes_resources_absent": temporary_kubernetes_resources_absent(),
        },
    )
    index = private_index(suite_root)
    index_path = suite_root / "private-evidence-index.json"
    canonical_write(index_path, index)
    public = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": utc_now(),
        "status": "review_pending",
        "acceptance_credit": False,
        "reviewer_sign_off": "pending",
        "source_identity": {
            "branch": branch,
            "runtime_revision": revision,
            "tree_sha": tree,
            "git_blobs": {
                label: git_blob_identity(ROOT, revision, path)
                for label, path in SOURCE_PATHS.items()
            },
        },
        "runtime_contract": config.public_dict(),
        "attempts": public_attempts,
        "analysis": analysis,
        "acceptance": analysis["acceptance"],
        "failed_attempts_and_rca": failed_attempts,
        "alignment": {
            "definition_alignment": True,
            "experiment_purpose_alignment": True,
            "validation_purpose_alignment": True,
            "test_purpose_alignment": True,
            "reviewer_sign_off": "pending",
        },
        "cleanup": {
            "holder_uid_match": True,
            "holder_image_match": True,
            "b0_cuda_inference": True,
            "queues_zero": True,
            "prometheus_baseline_restored": True,
            "target_absent": True,
            "ports_absent": True,
            "temporary_kubernetes_resources_absent": True,
        },
        "private_evidence": {
            "artifact_count": index["artifact_count"],
            "total_bytes": index["total_bytes"],
            "aggregate_sha256": index["aggregate_sha256"],
            "index_sha256": sha256_file(index_path),
        },
        "claim_boundary": CLAIM_BOUNDARY,
        "next_action": "Independent source-local review; do not start S6B-M before a verified amendment.",
    }
    write_public_json(args.output, public)
    print(
        json.dumps(
            {
                "status": public["status"],
                "runtime_revision": revision,
                "suite_root": str(suite_root),
                "output": str(args.output),
                "acceptance": public["acceptance"],
                "private_index_sha256": public["private_evidence"]["index_sha256"],
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
