from __future__ import annotations

import argparse
import json
import math
import os
import queue
import re
import socket
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
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
    assert_scale_validation_gpu_lease_owner,
    read_active_gpu_lease,
    release_scale_validation_gpu_lease,
)
from evm.scale_validation.x1_resume_testbed import (  # noqa: E402
    EXPECTED_MODELS,
    MANIFEST_SCHEMA_VERSION,
    CellSpec,
    X1ResumeConfig,
    X1ResumeTestbedError,
    canonical,
    canonical_sha256,
    canonical_write,
    deterministic_model_schedule,
    generate_report,
    request_interval_overlap,
    sha256_file,
    summarize_requests,
    triton_trace_compute_counts,
    validate_evidence,
)


CONTAINER_PREFIX = "evm-x1-resume-"
SERVING_URL = "http://127.0.0.1:30800"
SAMPLE_IMAGE_URI = "/mnt/evm-data/data/raw/industrial/visa/candle/Data/Images/Anomaly/000.JPG"
EXPECTED_PROMETHEUS_JOBS = {
    "evm-api",
    "evm-b0-production",
    "evm-otel-collector",
    "evm-task-queue-worker",
    "prometheus",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run X1 Resume Testbed v1 on the Windows host.")
    parser.add_argument(
        "--config",
        type=Path,
        default=ROOT / "configs/s8_v4_x1_resume_testbed_v1.toml",
    )
    parser.add_argument("--model-repository-root", type=Path, required=True)
    parser.add_argument("--private-base", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--report-output", type=Path, required=True)
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
        check=check,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def request_json(
    url: str, *, payload: dict[str, Any] | None = None, timeout: float = 10
) -> dict[str, Any]:
    response = requests.request(
        "POST" if payload is not None else "GET", url, json=payload, timeout=timeout
    )
    response.raise_for_status()
    value = response.json() if response.content else {}
    if not isinstance(value, dict):
        raise X1ResumeTestbedError(f"x1_resume_http_mapping:{url}")
    return value


def source_identity() -> dict[str, str]:
    status = run(["git", "status", "--porcelain"]).stdout
    if status:
        raise X1ResumeTestbedError("x1_resume_requires_clean_committed_worktree")
    return {
        "branch": run(["git", "branch", "--show-current"]).stdout.strip(),
        "revision": run(["git", "rev-parse", "HEAD"]).stdout.strip(),
        "tree_sha": run(["git", "rev-parse", "HEAD^{tree}"]).stdout.strip(),
    }


def git_blob_identity(relative: str) -> dict[str, str]:
    repository_root = Path(run(["git", "rev-parse", "--show-toplevel"]).stdout.strip())
    repository_relative = (
        (ROOT / relative).resolve().relative_to(repository_root.resolve()).as_posix()
    )
    blob = run(["git", "rev-parse", f"HEAD:{repository_relative}"]).stdout.strip()
    raw = run(["git", "show", f"HEAD:{repository_relative}"]).stdout.encode("utf-8")
    import hashlib

    return {"path": relative, "blob_oid": blob, "sha256": hashlib.sha256(raw).hexdigest()}


def load_and_validate_repository(
    root: Path, config: X1ResumeConfig
) -> tuple[dict[str, Any], dict[str, Any]]:
    manifest_path = root / "model-repository-manifest.json"
    samples_path = root / "testbed-samples.json"
    try:
        manifest = json.loads(manifest_path.read_bytes())
        samples = json.loads(samples_path.read_bytes())
    except (OSError, json.JSONDecodeError) as exc:
        raise X1ResumeTestbedError("x1_resume_repository_manifest_missing") from exc
    if (
        manifest.get("schema_version") != MANIFEST_SCHEMA_VERSION
        or manifest.get("config_sha256") != config.sha256
        or manifest.get("triton_image") != config.immutable_image
        or manifest.get("instance_kind") != "KIND_GPU"
        or manifest.get("cpu_fallback_allowed") is not False
        or tuple(manifest.get("model_ids", [])) != EXPECTED_MODELS
    ):
        raise X1ResumeTestbedError("x1_resume_repository_manifest_contract")
    entries = list(manifest.get("entries", []))
    for item in entries:
        relative = Path(str(item.get("path") or ""))
        if relative.is_absolute() or ".." in relative.parts:
            raise X1ResumeTestbedError("x1_resume_repository_entry_path")
        path = root / relative
        if (
            not path.is_file()
            or path.stat().st_size != item.get("bytes")
            or sha256_file(path) != item.get("sha256")
        ):
            raise X1ResumeTestbedError(f"x1_resume_repository_entry_digest:{relative}")
    if canonical_sha256(entries) != manifest.get("repository_sha256"):
        raise X1ResumeTestbedError("x1_resume_repository_aggregate")
    for profile in config.batching:
        selected = [item for item in entries if str(item["path"]).startswith(f"batch-{profile}/")]
        identity = dict(dict(manifest.get("profile_identities", {})).get(profile, {}))
        if identity.get("entry_count") != len(selected) or identity.get(
            "repository_sha256"
        ) != canonical_sha256(selected):
            raise X1ResumeTestbedError(f"x1_resume_profile_repository_identity:{profile}")
        for model_id in EXPECTED_MODELS:
            expected = dict(
                dict(manifest.get("model_identities", {})).get(f"{profile}:{model_id}", {})
            )
            artifact = next(
                item
                for item in selected
                if item["path"] == f"batch-{profile}/{model_id}/1/model.pt"
            )
            model_config = next(
                item
                for item in selected
                if item["path"] == f"batch-{profile}/{model_id}/config.pbtxt"
            )
            if expected != {
                "artifact_sha256": artifact["sha256"],
                "config_sha256": model_config["sha256"],
            }:
                raise X1ResumeTestbedError(f"x1_resume_profile_model_identity:{profile}:{model_id}")
    if sha256_file(samples_path) != manifest.get("samples_sha256"):
        raise X1ResumeTestbedError("x1_resume_samples_digest")
    return manifest, samples


def capture_gpu() -> dict[str, Any]:
    line = (
        run(
            [
                "nvidia-smi",
                "--query-gpu=uuid,name,memory.used,memory.total,utilization.gpu",
                "--format=csv,noheader,nounits",
            ]
        )
        .stdout.strip()
        .splitlines()
    )
    if len(line) != 1:
        raise X1ResumeTestbedError("x1_resume_single_gpu_required")
    values = [value.strip() for value in line[0].split(",")]
    return {
        "uuid": values[0],
        "name": values[1],
        "memory_used_mib": float(values[2]),
        "memory_total_mib": float(values[3]),
        "utilization_percent": float(values[4]),
    }


def capture_triton_processes() -> list[dict[str, Any]]:
    completed = run(
        [
            "nvidia-smi",
            "--query-compute-apps=pid,process_name,used_memory",
            "--format=csv,noheader,nounits",
        ],
        check=False,
    )
    result = []
    for line in completed.stdout.splitlines():
        values = [value.strip() for value in line.split(",")]
        if len(values) >= 2 and "tritonserver" in values[1].lower():
            result.append(
                {
                    "pid": values[0],
                    "process_name": values[1],
                    "used_memory_mib": values[2] if len(values) > 2 else "",
                }
            )
    return result


def prometheus_health() -> dict[str, Any]:
    payload = request_json("http://127.0.0.1:9090/api/v1/targets", timeout=10)
    targets = list(dict(payload.get("data", {})).get("activeTargets", []))
    governed = [
        item
        for item in targets
        if str(dict(item.get("labels", {})).get("job", "")) in EXPECTED_PROMETHEUS_JOBS
    ]
    return {
        "jobs": sorted(str(dict(item.get("labels", {})).get("job")) for item in governed),
        "total": len(governed),
        "up": sum(item.get("health") == "up" for item in governed),
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
    completed = run(
        [
            "docker",
            "exec",
            "evm-control-plane-postgres",
            "sh",
            "-lc",
            shell,
        ],
        check=False,
    )
    if completed.returncode != 0:
        raise X1ResumeTestbedError("x1_resume_queue_query_failed")
    parts = completed.stdout.strip().split("|")
    if len(parts) != 3:
        raise X1ResumeTestbedError("x1_resume_queue_query_shape")
    return {"active": int(parts[0]), "leased": int(parts[1]), "outcome_unknown": int(parts[2])}


def capture_holder() -> dict[str, Any]:
    payload = json.loads(
        run(
            ["kubectl", "-n", "evm-production", "get", "deployment/evm-b0-production", "-o", "json"]
        ).stdout
    )
    spec = dict(payload["spec"])
    status = dict(payload.get("status", {}))
    containers = list(dict(spec["template"])["spec"]["containers"])
    if (
        int(spec.get("replicas", 0)) != 1
        or int(status.get("readyReplicas", 0)) != 1
        or len(containers) != 1
    ):
        raise X1ResumeTestbedError("x1_resume_b0_not_exact_1_of_1")
    return {
        "uid": str(dict(payload["metadata"])["uid"]),
        "image": str(containers[0]["image"]),
        "replicas": 1,
    }


def scale_holder(replicas: int, timeout: float = 180) -> None:
    run(
        [
            "kubectl",
            "-n",
            "evm-production",
            "scale",
            "deployment/evm-b0-production",
            f"--replicas={replicas}",
        ]
    )
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        payload = json.loads(
            run(
                [
                    "kubectl",
                    "-n",
                    "evm-production",
                    "get",
                    "deployment/evm-b0-production",
                    "-o",
                    "json",
                ]
            ).stdout
        )
        spec = dict(payload["spec"])
        status = dict(payload.get("status", {}))
        desired = int(spec.get("replicas", 0))
        ready = int(status.get("readyReplicas", 0))
        available = int(status.get("availableReplicas", 0))
        if desired == replicas and ready == replicas and available == replicas:
            return
        if replicas == 0 and desired == 0 and ready == 0 and available == 0:
            return
        time.sleep(1)
    raise X1ResumeTestbedError(f"x1_resume_b0_scale_timeout:{replicas}")


def b0_cuda_check() -> dict[str, Any]:
    ready = request_json(f"{SERVING_URL}/ready", timeout=30)
    prediction = request_json(
        f"{SERVING_URL}/predict", payload={"image_uri": SAMPLE_IMAGE_URI}, timeout=30
    )
    passed = (
        ready.get("status") == "ok"
        and ready.get("model_loaded") is True
        and ready.get("device") == "cuda"
        and prediction.get("device") == "cuda"
        and bool(prediction.get("prediction"))
    )
    if not passed:
        raise X1ResumeTestbedError("x1_resume_b0_cuda_check")
    return {"passed": True, "ready": ready, "prediction": prediction}


def container_exists(name: str) -> bool:
    return run(["docker", "inspect", name], check=False, timeout=10).returncode == 0


def ports_absent(config: X1ResumeConfig) -> bool:
    for port in (config.http_port, config.grpc_port, config.metrics_port):
        with socket.socket() as probe:
            probe.settimeout(0.2)
            if probe.connect_ex(("127.0.0.1", port)) == 0:
                return False
    return True


def stop_container(name: str) -> None:
    if not container_exists(name):
        return
    run(["docker", "kill", "--signal", "SIGINT", name], check=False, timeout=30)
    deadline = time.monotonic() + 45
    while time.monotonic() < deadline and container_exists(name):
        time.sleep(0.5)
    if container_exists(name):
        run(["docker", "rm", "-f", name], check=False, timeout=30)


def triton_server_command(*, trace_enabled: bool) -> str:
    base = (
        "exec tritonserver --model-repository=/models --strict-readiness=true "
        "--allow-gpu-metrics=true --metrics-interval-ms=200 --log-format=ISO8601 "
        "--log-file=/evidence/triton.log"
    )
    if not trace_enabled:
        return base
    return (
        base + " --trace-config=mode=triton "
        "--trace-config=triton,file=/evidence/triton-trace.json "
        "--trace-config=level=TIMESTAMPS --trace-config=rate=64 --trace-config=count=-1"
    )


def start_triton(
    config: X1ResumeConfig,
    repository: Path,
    evidence_root: Path,
    name: str,
    *,
    trace_enabled: bool,
) -> None:
    command = triton_server_command(trace_enabled=trace_enabled)
    run(
        [
            "docker",
            "run",
            "-d",
            "--rm",
            "--name",
            name,
            "--gpus",
            "all",
            "--label",
            "evm.scenario=x1-resume-testbed-v1",
            "-p",
            f"127.0.0.1:{config.http_port}:8000",
            "-p",
            f"127.0.0.1:{config.grpc_port}:8001",
            "-p",
            f"127.0.0.1:{config.metrics_port}:8002",
            "-v",
            f"{repository}:/models:ro",
            "-v",
            f"{evidence_root}:/evidence",
            "--entrypoint",
            "bash",
            config.immutable_image,
            "-lc",
            command,
        ],
        timeout=90,
    )


def wait_ready(config: X1ResumeConfig, name: str) -> None:
    deadline = time.monotonic() + config.readiness_timeout_seconds
    urls = [
        f"http://127.0.0.1:{config.http_port}/v2/health/live",
        f"http://127.0.0.1:{config.http_port}/v2/health/ready",
        *[
            f"http://127.0.0.1:{config.http_port}/v2/models/{model_id}/ready"
            for model_id in EXPECTED_MODELS
        ],
    ]
    while time.monotonic() < deadline:
        try:
            if all(requests.get(url, timeout=1).status_code == 200 for url in urls):
                return
        except requests.RequestException:
            pass
        time.sleep(0.25)
    logs = run(["docker", "logs", "--tail", "200", name], check=False).stdout
    raise X1ResumeTestbedError(f"x1_resume_triton_readiness:{logs[-1000:]}")


def metric_values(config: X1ResumeConfig) -> tuple[str, dict[str, dict[str, float]]]:
    text = requests.get(f"http://127.0.0.1:{config.metrics_port}/metrics", timeout=10).text
    values = {
        model: {
            "success": 0.0,
            "compute_us": 0.0,
            "inference_count": 0.0,
            "execution_count": 0.0,
        }
        for model in EXPECTED_MODELS
    }
    names = {
        "nv_inference_request_success": "success",
        "nv_inference_compute_infer_duration_us": "compute_us",
        "nv_inference_count": "inference_count",
        "nv_inference_exec_count": "execution_count",
    }
    for line in text.splitlines():
        for metric, field in names.items():
            if not line.startswith(metric):
                continue
            for model in EXPECTED_MODELS:
                if f'model="{model}"' in line:
                    try:
                        values[model][field] += float(line.rsplit(" ", 1)[1])
                    except (IndexError, ValueError):
                        pass
    return text, values


def verify_model_configs(config: X1ResumeConfig, profile: str) -> dict[str, Any]:
    result = {}
    for model_id in EXPECTED_MODELS:
        payload = request_json(f"http://127.0.0.1:{config.http_port}/v2/models/{model_id}/config")
        groups = list(payload.get("instance_group", []))
        gpu_exact = (
            len(groups) == 1
            and groups[0].get("kind") == "KIND_GPU"
            and list(groups[0].get("gpus", [])) in ([0], [])
        )
        dynamic_present = bool(payload.get("dynamic_batching"))
        if not gpu_exact or dynamic_present != (profile == "on"):
            raise X1ResumeTestbedError(f"x1_resume_model_config_readback:{model_id}:{profile}")
        result[model_id] = {
            "config": payload,
            "gpu_instance_exact": True,
            "cpu_instance_present": False,
        }
    return result


class GpuSampler:
    def __init__(self, interval_ms: int) -> None:
        self.interval = interval_ms / 1000.0
        self.samples: list[dict[str, Any]] = []
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)

    def _run(self) -> None:
        while not self.stop_event.is_set():
            try:
                self.samples.append(capture_gpu())
            except Exception as exc:
                self.samples.append({"error": f"{type(exc).__name__}:{exc}"})
            self.stop_event.wait(self.interval)

    def __enter__(self) -> "GpuSampler":
        self.thread.start()
        return self

    def __exit__(self, *_: object) -> None:
        self.stop_event.set()
        self.thread.join(timeout=5)


def infer_once(
    config: X1ResumeConfig,
    model_id: str,
    values: list[float],
    request_id: str,
    batch_size: int = 1,
) -> tuple[int, list[float]]:
    payload = {
        "id": request_id,
        "inputs": [
            {
                "name": "FEATURES__0",
                "shape": [batch_size, len(values)],
                "datatype": "FP32",
                "data": values * batch_size,
            }
        ],
        "outputs": [{"name": "SCORE__0"}],
    }
    response = requests.post(
        f"http://127.0.0.1:{config.http_port}/v2/models/{model_id}/infer",
        json=payload,
        timeout=config.request_timeout_seconds,
    )
    status = response.status_code
    if status != 200:
        return status, []
    body = response.json()
    outputs = list(body.get("outputs", []))
    if len(outputs) != 1:
        raise X1ResumeTestbedError(f"x1_resume_inference_output:{model_id}")
    values_out = [float(item) for item in outputs[0].get("data", [])]
    if len(values_out) != batch_size or any(not math.isfinite(item) for item in values_out):
        raise X1ResumeTestbedError(f"x1_resume_inference_shape:{model_id}")
    return status, values_out


def run_q0(
    config: X1ResumeConfig,
    samples: dict[str, Any],
    manifest: dict[str, Any],
    model_configs: dict[str, Any],
    log_path: Path,
    raw_root: Path,
) -> list[dict[str, Any]]:
    q0 = []
    entries = {str(item["path"]): item for item in manifest["entries"]}
    for model_id in EXPECTED_MODELS:
        before_text, before = metric_values(config)
        values = list(samples["samples"][model_id][0])
        expected = float(samples["oracle"][model_id]["first_output"])
        q0_deadline = time.monotonic() + config.q0_activity_seconds_per_model
        with GpuSampler(config.sample_gpu_interval_ms) as sampler:

            def exercise(
                worker: int,
                target_model: str = model_id,
                target_values: list[float] = values,
                target_expected: float = expected,
                target_deadline: float = q0_deadline,
            ) -> int:
                index = 0
                while time.monotonic() < target_deadline:
                    status, output = infer_once(
                        config,
                        target_model,
                        target_values,
                        f"q0-{target_model}-{worker}-{index}-{uuid4().hex[:6]}",
                        batch_size=config.q0_request_batch_size,
                    )
                    if status != 200 or not math.isclose(
                        output[0], target_expected, rel_tol=1e-4, abs_tol=1e-4
                    ):
                        raise X1ResumeTestbedError(f"x1_resume_q0_oracle:{target_model}:{status}")
                    index += 1
                return index

            with ThreadPoolExecutor(max_workers=config.q0_workers) as pool:
                futures = [pool.submit(exercise, worker) for worker in range(config.q0_workers)]
                q0_request_count = sum(future.result() for future in futures)
        after_text, after = metric_values(config)
        compute_delta = after[model_id]["compute_us"] - before[model_id]["compute_us"]
        success_delta = after[model_id]["success"] - before[model_id]["success"]
        busy = [item for item in sampler.samples if float(item.get("utilization_percent", 0)) > 0]
        log_text = (
            log_path.read_text(encoding="utf-8", errors="replace") if log_path.is_file() else ""
        )
        gpu_lines = [
            line
            for line in log_text.splitlines()
            if model_id in line and re.search(r"GPU|device\s*0", line, re.IGNORECASE)
        ]
        artifact = entries[f"batch-off/{model_id}/1/model.pt"]
        model_config = entries[f"batch-off/{model_id}/config.pbtxt"]
        proof = (
            compute_delta > 0
            and success_delta > 0
            and bool(busy)
            and model_configs[model_id]["gpu_instance_exact"] is True
            and bool(gpu_lines)
        )
        if not proof:
            raise X1ResumeTestbedError(
                f"x1_resume_q0_cuda_attribution:{model_id}:{compute_delta}:{len(busy)}:{len(gpu_lines)}"
            )
        q0_item = {
            "model_id": model_id,
            "artifact_sha256": artifact["sha256"],
            "config_sha256": model_config["sha256"],
            "triton_config_readback": model_configs[model_id]["config"],
            "triton_gpu_instance_proof": True,
            "triton_success_delta": success_delta,
            "triton_compute_delta": compute_delta,
            "isolated_gpu_sample_count": len(sampler.samples),
            "isolated_gpu_busy_samples": len(busy),
            "isolated_request_count": q0_request_count,
            "request_batch_size": config.q0_request_batch_size,
            "gpu_log_line_sha256": [canonical_sha256(line) for line in gpu_lines],
            "metrics_before_sha256": canonical_sha256(before_text),
            "metrics_after_sha256": canonical_sha256(after_text),
            "cuda_activity_observed": True,
            "cpu_fallback_observed": False,
            "attribution_basis": "isolated-model window + exact KIND_GPU readback + GPU instance log + per-model Triton compute delta + nonzero device busy sample",
        }
        raw_path = raw_root / f"q0-{model_id}.json"
        if raw_path.exists():
            raise X1ResumeTestbedError(f"x1_resume_q0_raw_exists:{raw_path}")
        canonical_write(
            raw_path,
            {
                "schema_version": "evm.s8_v4.x1_resume_q0_raw.v1",
                "model_id": model_id,
                "metrics_before": before_text,
                "metrics_after": after_text,
                "gpu_samples": sampler.samples,
                "gpu_log_lines": gpu_lines,
                "isolated_request_count": q0_request_count,
            },
        )
        q0_item["private_raw"] = {
            "path": raw_path.relative_to(raw_root.parent).as_posix(),
            "bytes": raw_path.stat().st_size,
            "sha256": sha256_file(raw_path),
        }
        q0.append(q0_item)
    return q0


def run_cell(
    config: X1ResumeConfig,
    cell: CellSpec,
    repetition: int,
    samples: dict[str, Any],
    attempt_root: Path,
) -> dict[str, Any]:
    attempt_id = f"{cell.cell_id}-r{repetition}-{uuid4().hex[:8]}"
    queues = [queue.Queue(maxsize=config.queue_depth_per_api) for _ in range(cell.client_lanes)]
    records: list[dict[str, Any]] = []
    offered = admitted = rejected = 0
    lock = threading.Lock()
    abort_event = threading.Event()
    before_text, before_metrics = metric_values(config)
    sampler = GpuSampler(config.sample_gpu_interval_ms)
    model_schedule = deterministic_model_schedule(cell.model_mix)

    def worker(worker_id: int) -> None:
        assigned = queues[worker_id % len(queues)]
        session = requests.Session()
        while True:
            item = assigned.get()
            try:
                if item is None:
                    return
                if abort_event.is_set():
                    continue
                request_id, model_id, sample_index, enqueued_ns, measured = item
                started_ns = time.perf_counter_ns()
                outcome = "error"
                status = 0
                try:
                    values = list(samples["samples"][model_id][sample_index])
                    expected = float(samples["oracle"][model_id]["outputs"][sample_index])
                    payload = {
                        "id": request_id,
                        "inputs": [
                            {
                                "name": "FEATURES__0",
                                "shape": [1, len(values)],
                                "datatype": "FP32",
                                "data": values,
                            }
                        ],
                        "outputs": [{"name": "SCORE__0"}],
                    }
                    response = session.post(
                        f"http://127.0.0.1:{config.http_port}/v2/models/{model_id}/infer",
                        json=payload,
                        timeout=config.request_timeout_seconds,
                    )
                    status = response.status_code
                    if status == 200:
                        body = response.json()
                        output = float(list(body["outputs"])[0]["data"][0])
                        if not math.isfinite(output) or not math.isclose(
                            output, expected, rel_tol=1e-4, abs_tol=1e-4
                        ):
                            raise ValueError("oracle_mismatch")
                        outcome = "completed"
                    elif status >= 500:
                        outcome = "5xx"
                except Exception:
                    outcome = "5xx" if status >= 500 else "error"
                finished_ns = time.perf_counter_ns()
                if measured:
                    with lock:
                        records.append(
                            {
                                "request_id": request_id,
                                "model_id": model_id,
                                "worker_id": worker_id,
                                "outcome": outcome,
                                "status": status,
                                "started_ns": started_ns,
                                "finished_ns": finished_ns,
                                "queue_wait_ms": (started_ns - enqueued_ns) / 1e6,
                                "latency_ms": (finished_ns - enqueued_ns) / 1e6,
                            }
                        )
            finally:
                assigned.task_done()

    workers = [
        threading.Thread(target=worker, args=(index,), daemon=True)
        for index in range(cell.client_workers)
    ]
    for thread in workers:
        thread.start()
    started = time.perf_counter()
    total_seconds = config.warmup_seconds + config.measurement_seconds
    measurement_start_ns = int((started + config.warmup_seconds) * 1e9)
    measurement_end_ns = int((started + total_seconds) * 1e9)
    burst_size = cell.client_workers if cell.client_workers > 1 else 1
    cycle_period = burst_size / config.offered_rps
    next_cycle = started
    sequence = 0
    drain_seconds = 0.0
    drain_failure: str | None = None
    sampler.__enter__()
    try:
        while time.perf_counter() - started < total_seconds:
            remaining = next_cycle - time.perf_counter()
            if remaining > 0:
                time.sleep(remaining)
            elapsed = time.perf_counter() - started
            if elapsed >= total_seconds:
                break
            measured = elapsed >= config.warmup_seconds
            for _ in range(burst_size):
                model_id = model_schedule[sequence % len(model_schedule)]
                sample_index = sequence % int(samples["oracle"][model_id]["sample_count"])
                request_id = f"{attempt_id}-{sequence}"
                target = queues[sequence % len(queues)]
                if measured:
                    offered += 1
                try:
                    target.put_nowait(
                        (
                            request_id,
                            model_id,
                            sample_index,
                            time.perf_counter_ns(),
                            measured,
                        )
                    )
                    if measured:
                        admitted += 1
                except queue.Full:
                    if measured:
                        rejected += 1
                sequence += 1
            next_cycle += cycle_period
            if next_cycle < time.perf_counter():
                # Host scheduler lag lowers offered load; never issue an unbounded catch-up burst.
                next_cycle = time.perf_counter() + cycle_period
    finally:
        drain_started = time.monotonic()
        drain_deadline = drain_started + config.cleanup_timeout_seconds
        while (
            sum(item.unfinished_tasks for item in queues) > 0 and time.monotonic() < drain_deadline
        ):
            time.sleep(0.05)
        drain_seconds = time.monotonic() - drain_started
        if sum(item.unfinished_tasks for item in queues) > 0:
            drain_failure = "admitted_cohort_drain_timeout"
            abort_event.set()
            for assigned in queues:
                while True:
                    try:
                        assigned.get_nowait()
                    except queue.Empty:
                        break
                    else:
                        assigned.task_done()
        for index, _thread in enumerate(workers):
            try:
                queues[index % len(queues)].put(None, timeout=1)
            except queue.Full:
                drain_failure = drain_failure or "worker_stop_queue_full"
                abort_event.set()
        for thread in workers:
            thread.join(timeout=config.request_timeout_seconds + 2)
        if any(thread.is_alive() for thread in workers):
            drain_failure = drain_failure or "worker_liveness_timeout"
            abort_event.set()
        sampler.__exit__(None, None, None)
    if drain_failure is not None:
        raise X1ResumeTestbedError(
            f"x1_resume_bounded_drain:{cell.cell_id}:{repetition}:{drain_failure}"
        )
    after_text, after_metrics = metric_values(config)
    metrics = summarize_requests(
        offered=offered,
        admitted=admitted,
        local_admission_rejected=rejected,
        records=records,
        measurement_seconds=config.measurement_seconds,
        measurement_end_ns=measurement_end_ns,
        drain_seconds=drain_seconds,
        model_mix=cell.model_mix,
    )
    deltas = {
        model_id: {
            key: after_metrics[model_id][key] - before_metrics[model_id][key]
            for key in ("success", "compute_us", "inference_count", "execution_count")
        }
        for model_id in EXPECTED_MODELS
    }
    active_models = {model_id for model_id, fraction in cell.model_mix.items() if fraction > 0}
    triton_execution = all(
        deltas[model_id]["success"] > 0 and deltas[model_id]["compute_us"] > 0
        for model_id in active_models
    )
    if not triton_execution:
        raise X1ResumeTestbedError(f"x1_resume_cell_triton_execution:{cell.cell_id}:{repetition}")
    request_overlap = request_interval_overlap(records)
    overlap_required = cell.client_workers > 1 and len(active_models) > 1
    if overlap_required and request_overlap["observed"] is not True:
        raise X1ResumeTestbedError(
            f"x1_resume_cross_model_request_overlap:{cell.cell_id}:{repetition}"
        )
    inference_count = sum(deltas[model_id]["inference_count"] for model_id in active_models)
    execution_count = sum(deltas[model_id]["execution_count"] for model_id in active_models)
    formed_batch_size = inference_count / execution_count if execution_count > 0 else 0.0
    batching_proof = {
        "inference_count_delta": inference_count,
        "execution_count_delta": execution_count,
        "formed_mean_batch_size": formed_batch_size,
        "formed_batch_observed": formed_batch_size > 1.0,
    }
    if cell.cell_id == "balanced-concurrent-batch-on" and formed_batch_size <= 1.0:
        raise X1ResumeTestbedError(
            f"x1_resume_batch_not_formed:{cell.cell_id}:{repetition}:{formed_batch_size}"
        )
    raw = {
        "schema_version": "evm.s8_v4.x1_resume_attempt_raw.v1",
        "attempt_id": attempt_id,
        "cell": asdict(cell),
        "repetition": repetition,
        "records": records,
        "measurement_window": {
            "start_ns": measurement_start_ns,
            "end_ns": measurement_end_ns,
            "seconds": config.measurement_seconds,
        },
        "admission": {
            "offered": offered,
            "admitted": admitted,
            "local_admission_rejected": rejected,
        },
        "drain_seconds": drain_seconds,
        "metrics": metrics,
        "triton_metric_deltas": deltas,
        "cross_model_request_overlap": request_overlap,
        "batching_proof": batching_proof,
        "gpu_samples": sampler.samples,
        "metrics_before": before_text,
        "metrics_after": after_text,
    }
    raw_path = attempt_root / f"{attempt_id}.json"
    if raw_path.exists():
        raise X1ResumeTestbedError(f"x1_resume_attempt_raw_exists:{raw_path}")
    canonical_write(raw_path, raw)
    valid_gpu = [item for item in sampler.samples if "error" not in item]
    return {
        "attempt_id": attempt_id,
        "cell_id": cell.cell_id,
        "repetition": repetition,
        "batching": cell.batching,
        "client_topology": {
            "lanes": cell.client_lanes,
            "workers": cell.client_workers,
            "scope": "local client admission lanes and load-driver workers; not deployed API replicas",
            "offered_burst_size": burst_size,
            "cycle_period_seconds": cycle_period,
            "catch_up_disabled": True,
        },
        "analytical_roles": list(cell.analytical_roles),
        "model_mix": dict(cell.model_mix),
        "load_contract": {
            "target_offered_rps": config.offered_rps,
            "minimum_offered_rate_attainment": config.minimum_offered_rate_attainment,
            "matched_load_relative_tolerance": config.matched_load_relative_tolerance,
            "warmup_seconds": config.warmup_seconds,
            "measurement_seconds": config.measurement_seconds,
        },
        "metrics": metrics,
        "triton_metric_deltas": deltas,
        "triton_execution_proved": True,
        "cross_model_request_overlap": request_overlap,
        "cross_model_request_overlap_required": overlap_required,
        "batching_proof": batching_proof,
        "cpu_fallback_observed": False,
        "gpu": {
            "sample_count": len(valid_gpu),
            "utilization_max_percent": max(
                (float(item["utilization_percent"]) for item in valid_gpu), default=0.0
            ),
            "vram_max_mib": max(
                (float(item["memory_used_mib"]) for item in valid_gpu), default=0.0
            ),
        },
        "private_raw": {
            "path": raw_path.relative_to(attempt_root.parent).as_posix(),
            "bytes": raw_path.stat().st_size,
            "sha256": sha256_file(raw_path),
        },
    }


def wait_vram_restore(before: dict[str, Any], timeout: float) -> tuple[dict[str, Any], float]:
    started = time.monotonic()
    tolerance = max(256.0, float(before["memory_total_mib"]) * 0.05)
    last = capture_gpu()
    while time.monotonic() - started < timeout:
        last = capture_gpu()
        if abs(float(last["memory_used_mib"]) - float(before["memory_used_mib"])) <= tolerance:
            return last, time.monotonic() - started
        time.sleep(1)
    raise X1ResumeTestbedError(f"x1_resume_vram_restore:{last}")


def main() -> int:
    args = parse_args()
    if not args.maintenance_approved:
        raise X1ResumeTestbedError("x1_resume_maintenance_approval_required")
    if args.output == args.report_output:
        raise X1ResumeTestbedError("x1_resume_public_output_collision")
    for public_output in (args.output, args.report_output):
        if public_output.exists():
            raise X1ResumeTestbedError(f"x1_resume_public_output_exists:{public_output}")
    config = X1ResumeConfig.from_path(args.config)
    source = source_identity()
    manifest, samples = load_and_validate_repository(args.model_repository_root, config)
    if manifest.get("source_revision") != source["revision"]:
        raise X1ResumeTestbedError("x1_resume_repository_source_revision")
    image = json.loads(run(["docker", "image", "inspect", config.immutable_image]).stdout)
    if len(image) != 1:
        raise X1ResumeTestbedError("x1_resume_triton_image_identity")
    gpu_before = capture_gpu()
    if (gpu_before["uuid"], gpu_before["name"]) != (
        config.expected_gpu_uuid,
        config.expected_gpu_name,
    ):
        raise X1ResumeTestbedError("x1_resume_gpu_identity")
    if not ports_absent(config):
        raise X1ResumeTestbedError("x1_resume_ports_not_free")
    expected_container_names = [
        f"{CONTAINER_PREFIX}q0",
        *[f"{CONTAINER_PREFIX}{profile}" for profile in config.batching],
    ]
    stale = [name for name in expected_container_names if container_exists(name)]
    if stale:
        raise X1ResumeTestbedError(f"x1_resume_stale_containers:{','.join(stale)}")
    if read_active_gpu_lease() is not None:
        raise X1ResumeTestbedError("x1_resume_gpu_lease_preflight")
    triton_processes_before = capture_triton_processes()
    if triton_processes_before:
        raise X1ResumeTestbedError(
            f"x1_resume_triton_gpu_process_preflight:{triton_processes_before}"
        )
    queues_before = queue_counts()
    if any(queues_before.values()):
        raise X1ResumeTestbedError(f"x1_resume_queue_preflight:{queues_before}")
    prometheus_before = prometheus_health()
    if (
        set(prometheus_before["jobs"]) != EXPECTED_PROMETHEUS_JOBS
        or prometheus_before["total"] != 5
        or prometheus_before["up"] != 5
    ):
        raise X1ResumeTestbedError(f"x1_resume_prometheus_preflight:{prometheus_before}")
    holder = capture_holder()
    b0_before = b0_cuda_check()
    suite_id = f"x1-resume-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}-{source['revision'][:8]}"
    suite_root = args.private_base / suite_id
    suite_root.mkdir(parents=True, exist_ok=False)
    attempt_root = suite_root / "attempts"
    attempt_root.mkdir()
    lease = None
    active_containers: set[str] = set()
    q0: list[dict[str, Any]] = []
    summaries: list[dict[str, Any]] = []
    profile_evidence: dict[str, Any] = {}
    execution_error: Exception | None = None
    cleanup_errors: list[str] = []
    restore_required = False
    try:
        # Cleanup state is installed before the first mutation (B0 scale-down).
        restore_required = True
        scale_holder(0)
        lease = acquire_scale_validation_gpu_lease(
            suite_id,
            source_commit=source["revision"],
            purpose="scale_validation_inference",
            scenario_id="X1-RESUME",
            model_family="tabular",
            owner_pid=os.getpid(),
            ttl_seconds=7200,
        )
        assert_scale_validation_gpu_lease_owner(
            run_id=lease.run_id,
            lease_id=lease.lease_id,
            fencing_token=lease.fencing_token,
            purpose="scale_validation_inference",
            scenario_id="X1-RESUME",
            model_family="tabular",
        )
        q0_root = suite_root / "q0-isolated"
        q0_root.mkdir()
        q0_name = f"{CONTAINER_PREFIX}q0"
        active_containers.add(q0_name)
        start_triton(
            config,
            args.model_repository_root / "batch-off",
            q0_root,
            q0_name,
            trace_enabled=True,
        )
        wait_ready(config, q0_name)
        q0_configs = verify_model_configs(config, "off")
        q0 = run_q0(
            config,
            samples,
            manifest,
            q0_configs,
            q0_root / "triton.log",
            q0_root,
        )
        stop_container(q0_name)
        active_containers.discard(q0_name)
        q0_trace = q0_root / "triton-trace.json"
        q0_counts = triton_trace_compute_counts(q0_trace)
        q0_log = q0_root / "triton.log"
        profile_evidence["q0_isolated"] = {
            "trace": {
                "path": q0_trace.relative_to(suite_root).as_posix(),
                "sha256": sha256_file(q0_trace),
                "bytes": q0_trace.stat().st_size,
            },
            "log": {
                "path": q0_log.relative_to(suite_root).as_posix(),
                "sha256": sha256_file(q0_log),
                "bytes": q0_log.stat().st_size,
            },
            "compute_start_counts": q0_counts,
            "kernel_overlap_proved": False,
            "profiler_scope": "isolated Q0 Triton request timestamps only",
        }
        for item in q0:
            item["triton_trace_compute_start_count"] = int(q0_counts[item["model_id"]])
            if item["triton_trace_compute_start_count"] <= 0:
                raise X1ResumeTestbedError(f"x1_resume_q0_trace:{item['model_id']}")
        for profile in ("off", "on"):
            assert_scale_validation_gpu_lease_owner(
                run_id=lease.run_id,
                lease_id=lease.lease_id,
                fencing_token=lease.fencing_token,
                purpose="scale_validation_inference",
                scenario_id="X1-RESUME",
                model_family="tabular",
            )
            profile_root = suite_root / f"batch-{profile}"
            profile_root.mkdir()
            name = f"{CONTAINER_PREFIX}{profile}"
            active_containers.add(name)
            start_triton(
                config,
                args.model_repository_root / f"batch-{profile}",
                profile_root,
                name,
                trace_enabled=False,
            )
            wait_ready(config, name)
            verify_model_configs(config, profile)
            for cell in [item for item in config.cells if item.batching == profile]:
                for repetition in range(1, cell.repetitions + 1):
                    assert_scale_validation_gpu_lease_owner(
                        run_id=lease.run_id,
                        lease_id=lease.lease_id,
                        fencing_token=lease.fencing_token,
                        purpose="scale_validation_inference",
                        scenario_id="X1-RESUME",
                        model_family="tabular",
                    )
                    summaries.append(run_cell(config, cell, repetition, samples, attempt_root))
            stop_container(name)
            active_containers.discard(name)
            log_path = profile_root / "triton.log"
            profile_evidence[profile] = {
                "log": {
                    "path": log_path.relative_to(suite_root).as_posix(),
                    "sha256": sha256_file(log_path),
                    "bytes": log_path.stat().st_size,
                },
                "triton_timestamp_trace_enabled": False,
                "kernel_overlap_proved": False,
                "profiler_scope": (
                    "disabled for bounded timed-matrix evidence; request intervals and "
                    "metric deltas captured"
                ),
            }
    except Exception as exc:
        execution_error = exc
    finally:
        for name in sorted(active_containers):
            try:
                stop_container(name)
            except Exception as exc:
                cleanup_errors.append(f"container:{name}:{type(exc).__name__}:{exc}")
        if lease is not None:
            try:
                release_scale_validation_gpu_lease(
                    run_id=lease.run_id,
                    lease_id=lease.lease_id,
                    fencing_token=lease.fencing_token,
                    reason=f"{suite_id} finished",
                )
            except Exception as exc:
                cleanup_errors.append(f"lease:{type(exc).__name__}:{exc}")
        if restore_required:
            try:
                scale_holder(1)
            except Exception as exc:
                cleanup_errors.append(f"b0_restore:{type(exc).__name__}:{exc}")
    # Every final check runs independently so one failure cannot suppress the others.
    final_checks: dict[str, Any] = {}
    checks = {
        "holder": lambda: capture_holder(),
        "b0_cuda": b0_cuda_check,
        "queues": queue_counts,
        "prometheus": prometheus_health,
        "gpu": capture_gpu,
        "triton_processes": capture_triton_processes,
    }
    for key, operation in checks.items():
        try:
            final_checks[key] = operation()
        except Exception as exc:
            cleanup_errors.append(f"check:{key}:{type(exc).__name__}:{exc}")
    try:
        gpu_after, vram_seconds = wait_vram_restore(gpu_before, config.cleanup_timeout_seconds)
        final_checks["gpu_after_vram_wait"] = gpu_after
        final_checks["vram_restore_seconds"] = vram_seconds
    except Exception as exc:
        cleanup_errors.append(f"check:vram:{type(exc).__name__}:{exc}")
    cleanup = {
        "container_absent": all(not container_exists(name) for name in expected_container_names),
        "ports_absent": ports_absent(config),
        "gpu_lease_absent": read_active_gpu_lease() is None,
        "triton_gpu_process_residue": final_checks.get("triton_processes", []),
        "b0_identity_restored": final_checks.get("holder") == holder,
        "b0_cuda_restored": dict(final_checks.get("b0_cuda", {})).get("passed") is True,
        "queue_active_zero": dict(final_checks.get("queues", {})).get("active") == 0,
        "queue_leased_zero": dict(final_checks.get("queues", {})).get("leased") == 0,
        "queue_outcome_unknown_zero": dict(final_checks.get("queues", {})).get("outcome_unknown")
        == 0,
        "prometheus_5_of_5": dict(final_checks.get("prometheus", {})).get("total") == 5
        and dict(final_checks.get("prometheus", {})).get("up") == 5,
        "prometheus_exact_jobs_restored": set(
            dict(final_checks.get("prometheus", {})).get("jobs", [])
        )
        == EXPECTED_PROMETHEUS_JOBS,
        "errors": cleanup_errors,
    }
    cleanup_path = suite_root / "cleanup.json"
    canonical_write(cleanup_path, {"cleanup": cleanup, "final_checks": final_checks})
    if (
        execution_error is not None
        or cleanup_errors
        or not all(
            cleanup[key]
            for key in (
                "container_absent",
                "ports_absent",
                "gpu_lease_absent",
                "b0_identity_restored",
                "b0_cuda_restored",
                "queue_active_zero",
                "queue_leased_zero",
                "queue_outcome_unknown_zero",
                "prometheus_5_of_5",
                "prometheus_exact_jobs_restored",
            )
        )
        or cleanup["triton_gpu_process_residue"]
    ):
        failure = {
            "schema_version": "evm.s8_v4.x1_resume_failure.v1",
            "suite_id": suite_id,
            "credit": "zero_credit",
            "failure": f"{type(execution_error).__name__}:{execution_error}"
            if execution_error
            else "cleanup_failed",
            "cleanup": cleanup,
        }
        canonical_write(suite_root / "failure.json", failure)
        raise X1ResumeTestbedError(
            f"x1_resume_execution_failed:{failure['failure']}:{cleanup_errors}"
        )
    public = {
        "schema_version": "evm.s8_v4.x1_resume_testbed.v1",
        "suite_id": suite_id,
        "status": "complete",
        "claim_class": "preliminary_controlled_testbed",
        "credit": "non_credit",
        "canonical_x1": False,
        "acceptance_credit": False,
        "config_sha256": config.sha256,
        "source_identity": source,
        "source_blobs": [
            git_blob_identity(".gitattributes"),
            git_blob_identity("configs/s8_v4_x1_resume_testbed_v1.toml"),
            git_blob_identity("src/evm/control_panel/scenario_workloads.py"),
            git_blob_identity("src/evm/scale_validation/x1_resume_testbed.py"),
            git_blob_identity("scripts/dev/prepare_s8_v4_x1_resume_testbed.py"),
            git_blob_identity("scripts/dev/run_s8_v4_x1_resume_testbed.py"),
            git_blob_identity("scripts/dev/validate_s8_v4_x1_resume_testbed.py"),
        ],
        "environment": {
            "gpu_before": gpu_before,
            "triton_processes_before": triton_processes_before,
            "triton_image": config.immutable_image,
            "repository_manifest_sha256": sha256_file(
                args.model_repository_root / "model-repository-manifest.json"
            ),
            "repository_sha256": manifest["repository_sha256"],
            "b0_before": {"holder": holder, "cuda": b0_before},
            "gpu_lease": {
                "lease_id": lease.lease_id,
                "run_id": lease.run_id,
                "scenario_id": lease.scenario_id,
                "purpose": lease.lease_purpose,
                "source_commit": lease.source_commit,
                "fencing_token_sha256": canonical_sha256(lease.fencing_token),
            },
        },
        "q0": q0,
        "runs": summaries,
        "profile_evidence": profile_evidence,
        "profiler": {
            "status": "triton_timestamps_captured",
            "kernel_overlap_proved": False,
            "claim": "No kernel-overlap claim; a direct CUDA profiler is a follow-up gate.",
        },
        "cleanup": cleanup,
        "cleanup_evidence": {
            "path": cleanup_path.relative_to(suite_root).as_posix(),
            "bytes": cleanup_path.stat().st_size,
            "sha256": sha256_file(cleanup_path),
            "final_checks_sha256": canonical_sha256(final_checks),
        },
        "claim_boundary": config.claim_boundary,
    }
    validate_evidence(
        public,
        config,
        private_suite_root=suite_root,
        model_repository_root=args.model_repository_root,
        source_root=ROOT,
    )
    report = generate_report(
        public,
        config,
        private_suite_root=suite_root,
        model_repository_root=args.model_repository_root,
        source_root=ROOT,
    )
    canonical_write(args.output, public)
    canonical_write(args.report_output, report)
    print(
        canonical(
            {
                "suite_id": suite_id,
                "evidence": str(args.output),
                "report": str(args.report_output),
                "physical_runs": len(summaries),
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
