from __future__ import annotations

import argparse
import hashlib
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
from collections.abc import Mapping
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
    gpu_lease_root,
    read_active_gpu_lease,
    release_scale_validation_gpu_lease,
)
from evm.scale_validation.x1_resume_testbed import (  # noqa: E402
    DEFAULT_CONFIG_RELATIVE_PATH,
    EXPECTED_MODELS,
    EXPECTED_PROMETHEUS_JOBS,
    REQUIRED_SOURCE_BLOB_PATHS,
    CellSpec,
    X1ResumeConfig,
    X1ResumeTestbedError,
    _validate_manifest_contract,
    _validate_attempt_records,
    _triton_metric_deltas,
    _triton_metrics_for_model,
    canonical,
    canonical_sha256,
    canonical_write,
    canonical_write_once,
    deterministic_model_schedule,
    ensure_distinct_output_targets,
    generate_report,
    load_canonical_json,
    prometheus_baseline_ready,
    render_triton_server_command,
    require_default_config_path,
    request_interval_overlap,
    sha256_file,
    summarize_requests,
    triton_trace_compute_counts,
    triton_gpu_instance_exact,
    triton_model_metadata_version_exact,
    triton_repository_index_exact,
    triton_repository_index_full,
    validate_evidence,
    validate_governed_source_bindings,
    validate_gpu_samples,
    validate_repository_entries,
    validate_sample_payload,
    validate_triton_container_mounts,
    wait_for_prometheus_baseline,
)


CONTAINER_PREFIX = "evm-x1-resume-"
SERVING_URL = "http://127.0.0.1:30800"
SAMPLE_IMAGE_URI = "/mnt/evm-data/data/raw/industrial/visa/candle/Data/Images/Anomaly/000.JPG"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run X1 Resume Testbed v1 on the Windows host.")
    parser.add_argument(
        "--config",
        type=Path,
        default=ROOT / DEFAULT_CONFIG_RELATIVE_PATH,
    )
    parser.add_argument("--model-repository-root", type=Path, required=True)
    parser.add_argument("--data-root", type=Path, required=True)
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


def git_blob_identity(relative: str, revision: str) -> dict[str, str]:
    repository_root = Path(run(["git", "rev-parse", "--show-toplevel"]).stdout.strip())
    path = (ROOT / relative).resolve()
    repository_relative = path.relative_to(repository_root.resolve()).as_posix()
    blob = run(["git", "rev-parse", f"{revision}:{repository_relative}"]).stdout.strip()
    raw = subprocess.run(
        ["git", "show", f"{revision}:{repository_relative}"],
        cwd=ROOT,
        check=True,
        capture_output=True,
    ).stdout
    return {
        "path": relative,
        "source_revision": revision,
        "blob_oid": blob,
        "sha256": hashlib.sha256(raw).hexdigest(),
        "working_sha256": sha256_file(path),
    }


def load_and_validate_repository(
    root: Path, config: X1ResumeConfig, data_root: Path
) -> tuple[dict[str, Any], dict[str, Any]]:
    manifest_path = root / "model-repository-manifest.json"
    samples_path = root / "testbed-samples.json"
    try:
        manifest = load_canonical_json(manifest_path, label="model_repository_manifest")
        samples = validate_sample_payload(samples_path, config)
    except (OSError, X1ResumeTestbedError) as exc:
        raise X1ResumeTestbedError("x1_resume_repository_manifest_missing") from exc
    try:
        _validate_manifest_contract(manifest, config)
    except X1ResumeTestbedError as exc:
        raise X1ResumeTestbedError("x1_resume_repository_manifest_contract") from exc
    profile_identities = dict(manifest["profile_identities"])
    model_identities = dict(manifest["model_identities"])
    validate_governed_source_bindings(manifest, data_root=data_root, config=config)
    entries = validate_repository_entries(manifest, root, config=config)
    if canonical_sha256(entries) != manifest.get("repository_sha256"):
        raise X1ResumeTestbedError("x1_resume_repository_aggregate")
    for profile in config.batching:
        selected = [item for item in entries if str(item["path"]).startswith(f"batch-{profile}/")]
        identity = dict(profile_identities.get(profile, {}))
        if identity.get("entry_count") != len(selected) or identity.get(
            "repository_sha256"
        ) != canonical_sha256(selected):
            raise X1ResumeTestbedError(f"x1_resume_profile_repository_identity:{profile}")
        for model_id in EXPECTED_MODELS:
            expected = dict(model_identities.get(f"{profile}:{model_id}", {}))
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


def prometheus_health(*, timeout: float = 10.0) -> dict[str, Any]:
    payload = request_json("http://127.0.0.1:9090/api/v1/targets", timeout=timeout)
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


def assert_prometheus_preflight(snapshot: dict[str, Any]) -> None:
    if not prometheus_baseline_ready(snapshot, EXPECTED_PROMETHEUS_JOBS):
        raise X1ResumeTestbedError(f"x1_resume_prometheus_preflight:{snapshot}")


def wait_prometheus_restore(
    timeout_seconds: float,
) -> tuple[dict[str, Any], float, list[dict[str, Any]], bool, str]:
    return wait_for_prometheus_baseline(
        lambda remaining: prometheus_health(timeout=min(10.0, remaining)),
        EXPECTED_PROMETHEUS_JOBS,
        timeout_seconds=timeout_seconds,
        poll_interval_seconds=1.0,
        monotonic=time.monotonic,
        sleep=time.sleep,
        observed_at=utc_now,
    )


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


def capture_container_state(names: list[str]) -> dict[str, Any]:
    return {
        "expected_names": names,
        "present_names": [name for name in names if container_exists(name)],
    }


def capture_port_state(config: X1ResumeConfig) -> dict[str, Any]:
    expected_ports = [config.http_port, config.grpc_port, config.metrics_port]
    listening_ports = []
    for port in expected_ports:
        with socket.socket() as probe:
            probe.settimeout(0.2)
            if probe.connect_ex(("127.0.0.1", port)) == 0:
                listening_ports.append(port)
    return {"expected_ports": expected_ports, "listening_ports": listening_ports}


def capture_gpu_lease_state() -> dict[str, Any]:
    lease = read_active_gpu_lease()
    return {"active": lease.model_dump(mode="json") if lease is not None else None}


def ports_absent(config: X1ResumeConfig) -> bool:
    return capture_port_state(config)["listening_ports"] == []


def stop_container(name: str) -> None:
    if not container_exists(name):
        return
    run(["docker", "kill", "--signal", "SIGINT", name], check=False, timeout=30)
    deadline = time.monotonic() + 45
    while time.monotonic() < deadline and container_exists(name):
        time.sleep(0.5)
    if container_exists(name):
        run(["docker", "rm", "-f", name], check=False, timeout=30)


def start_triton(
    config: X1ResumeConfig,
    repository: Path,
    evidence_root: Path,
    name: str,
    *,
    trace_enabled: bool,
) -> dict[str, Any]:
    command = render_triton_server_command(trace_enabled=trace_enabled)
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
            f"{evidence_root}:/evidence:rw",
            "--entrypoint",
            "bash",
            config.immutable_image,
            "-lc",
            command,
        ],
        timeout=90,
    )
    inspected = json.loads(run(["docker", "inspect", name], timeout=30).stdout)
    if (
        not isinstance(inspected, list)
        or len(inspected) != 1
        or not isinstance(inspected[0], Mapping)
    ):
        raise X1ResumeTestbedError("x1_resume_container_inspect")
    mounts = validate_triton_container_mounts(
        inspected[0].get("Mounts"), repository=repository, evidence_root=evidence_root
    )
    return {"server_command": command, "mounts": mounts}


def fetch_repository_index(config: X1ResumeConfig, *, timeout: float = 1.0) -> Any:
    response = requests.post(
        f"http://127.0.0.1:{config.http_port}/v2/repository/index",
        json={},
        timeout=timeout,
    )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, list):
        return payload
    return [
        {**item, "reason": ""}
        if isinstance(item, Mapping) and set(item) == {"name", "version", "state"}
        else item
        for item in payload
    ]


def wait_ready(config: X1ResumeConfig, name: str) -> dict[str, Any]:
    deadline = time.monotonic() + config.readiness_timeout_seconds
    last_index: Any = None
    first_full_index: list[dict[str, Any]] | None = None
    last_metadata: dict[str, Any] = {}
    while time.monotonic() < deadline:
        try:
            live = requests.get(f"http://127.0.0.1:{config.http_port}/v2/health/live", timeout=1)
            if live.status_code == 200:
                last_index = fetch_repository_index(config)
                if triton_repository_index_full(last_index):
                    if first_full_index is None:
                        first_full_index = [dict(item) for item in last_index]
                if triton_repository_index_exact(last_index):
                    last_metadata = {
                        model.model_id: {
                            "endpoint": f"/v2/models/{model.model_id}/versions/1",
                            "payload": request_json(
                                f"http://127.0.0.1:{config.http_port}/v2/models/"
                                f"{model.model_id}/versions/1",
                                timeout=1,
                            ),
                        }
                        for model in config.models
                    }
                    server_ready = requests.get(
                        f"http://127.0.0.1:{config.http_port}/v2/health/ready",
                        timeout=1,
                    )
                    model_ready = {
                        model_id: requests.get(
                            f"http://127.0.0.1:{config.http_port}/v2/models/"
                            f"{model_id}/versions/1/ready",
                            timeout=1,
                        )
                        for model_id in EXPECTED_MODELS
                    }
                    if (
                        all(
                            triton_model_metadata_version_exact(
                                last_metadata[model.model_id]["payload"],
                                model_id=model.model_id,
                                input_width=model.input_width,
                            )
                            for model in config.models
                        )
                        and server_ready.status_code == 200
                        and all(response.status_code == 200 for response in model_ready.values())
                    ):
                        return {
                            "server_health": {
                                "live": {"endpoint": "/v2/health/live", "status": 200},
                                "ready": {
                                    "endpoint": "/v2/health/ready",
                                    "status": server_ready.status_code,
                                },
                            },
                            "repository_index_full": first_full_index,
                            "repository_index_ready": [dict(item) for item in last_index],
                            "model_ready": {
                                model_id: {
                                    "endpoint": f"/v2/models/{model_id}/versions/1/ready",
                                    "status": response.status_code,
                                }
                                for model_id, response in model_ready.items()
                            },
                            "model_metadata": last_metadata,
                        }
        except requests.RequestException:
            pass
        time.sleep(0.25)
    logs = run(["docker", "logs", "--tail", "200", name], check=False).stdout
    raise X1ResumeTestbedError(
        f"x1_resume_triton_readiness:{last_index}:{last_metadata}:{logs[-1000:]}"
    )


def metric_values(config: X1ResumeConfig) -> tuple[str, dict[str, dict[str, float]]]:
    response = requests.get(f"http://127.0.0.1:{config.metrics_port}/metrics", timeout=10)
    response.raise_for_status()
    text = response.text
    return text, {model: _triton_metrics_for_model(text, model) for model in EXPECTED_MODELS}


def verify_model_configs(config: X1ResumeConfig, profile: str) -> dict[str, Any]:
    result = {}
    for model_id in EXPECTED_MODELS:
        payload = request_json(
            f"http://127.0.0.1:{config.http_port}/v2/models/{model_id}/versions/1/config"
        )
        gpu_exact = triton_gpu_instance_exact(
            payload,
            model_id=model_id,
            input_width=next(
                model.input_width for model in config.models if model.model_id == model_id
            ),
            batching=config.batching[profile],
        )
        if not gpu_exact:
            raise X1ResumeTestbedError(f"x1_resume_model_config_readback:{model_id}:{profile}")
        result[model_id] = {
            "config": payload,
            "gpu_instance_exact": True,
            "cpu_instance_present": False,
        }
    return result


def write_runtime_contract(
    *,
    path: Path,
    suite_root: Path,
    profile: str,
    batching: str,
    start_evidence: Mapping[str, Any],
    readiness: Mapping[str, Any],
    model_configs: Mapping[str, Any],
) -> dict[str, Any]:
    payload = {
        "schema_version": "evm.s8_v4.x1_resume_runtime_contract.v1",
        "profile": profile,
        "batching": batching,
        "server_command": start_evidence["server_command"],
        "mounts": start_evidence["mounts"],
        "runtime_readiness": readiness,
        "model_configs": {
            model_id: {
                "endpoint": f"/v2/models/{model_id}/versions/1/config",
                "payload": item["config"],
            }
            for model_id, item in model_configs.items()
        },
    }
    canonical_write_once(path, payload)
    return {
        "path": path.relative_to(suite_root).as_posix(),
        "sha256": sha256_file(path),
        "bytes": path.stat().st_size,
    }


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
        f"http://127.0.0.1:{config.http_port}/v2/models/{model_id}/versions/1/infer",
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
        metric_deltas = _triton_metric_deltas(before[model_id], after[model_id], model_id=model_id)
        compute_delta = metric_deltas["compute_us"]
        success_delta = metric_deltas["success"]
        inference_count_delta = metric_deltas["inference_count"]
        execution_count_delta = metric_deltas["execution_count"]
        gpu_summary = validate_gpu_samples(sampler.samples, config, label=f"q0:{model_id}")
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
            and success_delta == q0_request_count
            and inference_count_delta == q0_request_count * config.q0_request_batch_size
            and execution_count_delta == q0_request_count
            and gpu_summary["busy_sample_count"] > 0
            and model_configs[model_id]["gpu_instance_exact"] is True
            and bool(gpu_lines)
        )
        if not proof:
            raise X1ResumeTestbedError(
                f"x1_resume_q0_cuda_attribution:{model_id}:{compute_delta}:"
                f"{gpu_summary['busy_sample_count']}:{len(gpu_lines)}"
            )
        q0_item = {
            "model_id": model_id,
            "artifact_sha256": artifact["sha256"],
            "config_sha256": model_config["sha256"],
            "triton_config_readback": model_configs[model_id]["config"],
            "triton_gpu_instance_proof": True,
            "triton_success_delta": success_delta,
            "triton_compute_delta": compute_delta,
            "triton_inference_count_delta": inference_count_delta,
            "triton_execution_count_delta": execution_count_delta,
            "isolated_gpu_sample_count": gpu_summary["sample_count"],
            "isolated_gpu_busy_samples": gpu_summary["busy_sample_count"],
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
    suite_id: str,
) -> dict[str, Any]:
    attempt_id = f"{suite_id}-{cell.cell_id}-r{repetition}-{uuid4().hex[:8]}"
    queues = [queue.Queue(maxsize=config.queue_depth_per_api) for _ in range(cell.client_lanes)]
    records: list[dict[str, Any]] = []
    terminal_records: list[dict[str, Any]] = []
    admission_ledger: list[dict[str, Any]] = []
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
                (
                    request_id,
                    model_id,
                    sample_index,
                    enqueued_ns,
                    phase,
                    global_sequence,
                ) = item
                started_ns = time.perf_counter_ns()
                outcome = "error"
                status = 0
                oracle_valid = False
                expected_output: float | None = None
                observed_output: float | None = None
                try:
                    values = list(samples["samples"][model_id][sample_index])
                    expected_output = float(samples["oracle"][model_id]["outputs"][sample_index])
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
                        f"http://127.0.0.1:{config.http_port}/v2/models/{model_id}/versions/1/infer",
                        json=payload,
                        timeout=config.request_timeout_seconds,
                    )
                    status = response.status_code
                    if status == 200:
                        body = response.json()
                        observed_output = float(list(body["outputs"])[0]["data"][0])
                        if not math.isfinite(observed_output) or not math.isclose(
                            observed_output,
                            expected_output,
                            rel_tol=1e-4,
                            abs_tol=1e-4,
                        ):
                            raise ValueError("oracle_mismatch")
                        oracle_valid = True
                        outcome = "completed"
                    elif status >= 500:
                        outcome = "5xx"
                except Exception:
                    outcome = "5xx" if status >= 500 else "error"
                finished_ns = time.perf_counter_ns()
                projected = {
                    "request_id": request_id,
                    "model_id": model_id,
                    "worker_id": worker_id,
                    "outcome": outcome,
                    "status": status,
                    "enqueued_ns": enqueued_ns,
                    "started_ns": started_ns,
                    "finished_ns": finished_ns,
                    "queue_wait_ms": (started_ns - enqueued_ns) / 1e6,
                    "latency_ms": (finished_ns - started_ns) / 1e6,
                    "oracle_valid": oracle_valid,
                    "expected_output": expected_output,
                    "observed_output": observed_output,
                }
                with lock:
                    terminal_records.append(
                        {
                            **projected,
                            "global_sequence": global_sequence,
                            "phase": phase,
                        }
                    )
                    if phase == "measured":
                        records.append(projected)
            finally:
                assigned.task_done()

    workers = [
        threading.Thread(target=worker, args=(index,), daemon=True)
        for index in range(cell.client_workers)
    ]
    for thread in workers:
        thread.start()
    started_ns = time.perf_counter_ns()
    started = started_ns / 1e9
    total_seconds = config.warmup_seconds + config.measurement_seconds
    measurement_start_ns = started_ns + config.warmup_seconds * 1_000_000_000
    measurement_end_ns = measurement_start_ns + config.measurement_seconds * 1_000_000_000
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
            for _ in range(burst_size):
                enqueued_ns = time.perf_counter_ns()
                if enqueued_ns >= measurement_end_ns:
                    break
                phase = "measured" if measurement_start_ns <= enqueued_ns else "warmup"
                model_id = model_schedule[sequence % len(model_schedule)]
                sample_index = sequence % int(samples["oracle"][model_id]["sample_count"])
                request_id = f"{attempt_id}-{sequence}"
                target = queues[sequence % len(queues)]
                if phase == "measured":
                    offered += 1
                decision_ns = time.perf_counter_ns()
                try:
                    target.put_nowait(
                        (
                            request_id,
                            model_id,
                            sample_index,
                            enqueued_ns,
                            phase,
                            sequence,
                        )
                    )
                    decision = "accepted"
                    reason = "local_queue_capacity"
                    if phase == "measured":
                        admitted += 1
                except queue.Full:
                    decision = "rejected"
                    reason = "local_queue_full"
                    if phase == "measured":
                        rejected += 1
                admission_ledger.append(
                    {
                        "global_sequence": sequence,
                        "request_id": request_id,
                        "model_id": model_id,
                        "phase": phase,
                        "enqueued_ns": enqueued_ns,
                        "decision_ns": decision_ns,
                        "decision": decision,
                        "reason": reason,
                    }
                )
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
    gpu_summary = validate_gpu_samples(sampler.samples, config, label=f"attempt:{attempt_id}")
    after_text, after_metrics = metric_values(config)
    metrics = summarize_requests(
        offered=offered,
        admitted=admitted,
        local_admission_rejected=rejected,
        records=records,
        measurement_seconds=config.measurement_seconds,
        measurement_start_ns=measurement_start_ns,
        measurement_end_ns=measurement_end_ns,
        drain_seconds=drain_seconds,
        model_mix=cell.model_mix,
    )
    deltas = {
        model_id: _triton_metric_deltas(
            before_metrics[model_id], after_metrics[model_id], model_id=model_id
        )
        for model_id in EXPECTED_MODELS
    }
    (
        _measured_completed_by_model,
        completed_by_model,
        _request_ids,
        admission_proof,
    ) = _validate_attempt_records(
        records,
        terminal_records,
        admission_ledger,
        attempt_id=attempt_id,
        model_mix=cell.model_mix,
        warmup_seconds=config.warmup_seconds,
        offered_rps=config.offered_rps,
        minimum_offered_rate_attainment=config.minimum_offered_rate_attainment,
        matched_load_relative_tolerance=config.matched_load_relative_tolerance,
        measurement_start_ns=measurement_start_ns,
        measurement_end_ns=measurement_end_ns,
        admission={
            "offered": offered,
            "admitted": admitted,
            "local_admission_rejected": rejected,
        },
    )
    for model_id in EXPECTED_MODELS:
        completed = completed_by_model[model_id]
        values = deltas[model_id]
        if (
            values["success"] != completed
            or values["inference_count"] != completed
            or values["execution_count"] > values["inference_count"]
            or (completed > 0 and values["execution_count"] <= 0)
            or (completed > 0 and values["compute_us"] <= 0)
            or (completed == 0 and any(value != 0 for value in values.values()))
        ):
            raise X1ResumeTestbedError(
                f"x1_resume_cell_triton_arithmetic:{cell.cell_id}:{repetition}:{model_id}"
            )
    active_models = {model_id for model_id, fraction in cell.model_mix.items() if fraction > 0}
    triton_execution = all(
        deltas[model_id]["success"] > 0 and deltas[model_id]["compute_us"] > 0
        for model_id in active_models
    )
    if not triton_execution:
        raise X1ResumeTestbedError(f"x1_resume_cell_triton_execution:{cell.cell_id}:{repetition}")
    request_overlap = request_interval_overlap(
        records,
        measurement_start_ns=measurement_start_ns,
        measurement_end_ns=measurement_end_ns,
    )
    overlap_required = cell.client_workers > 1 and len(active_models) > 1
    if overlap_required and request_overlap["observed"] is not True:
        raise X1ResumeTestbedError(
            f"x1_resume_cross_model_request_overlap:{cell.cell_id}:{repetition}"
        )
    inference_count = sum(deltas[model_id]["inference_count"] for model_id in active_models)
    execution_count = sum(deltas[model_id]["execution_count"] for model_id in active_models)
    formed_batch_size = inference_count / execution_count if execution_count > 0 else 0.0
    if not 0 < formed_batch_size <= 32:
        raise X1ResumeTestbedError(f"x1_resume_cell_batch_arithmetic:{cell.cell_id}:{repetition}")
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
        "terminal_records": terminal_records,
        "admission_ledger": admission_ledger,
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
        "admission_proof": admission_proof,
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
        "admission_proof": admission_proof,
        "triton_metric_deltas": deltas,
        "triton_execution_proved": True,
        "cross_model_request_overlap": request_overlap,
        "cross_model_request_overlap_required": overlap_required,
        "batching_proof": batching_proof,
        "cpu_fallback_observed": False,
        "gpu": {
            "sample_count": gpu_summary["sample_count"],
            "utilization_max_percent": gpu_summary["utilization_max_percent"],
            "vram_max_mib": gpu_summary["vram_max_mib"],
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
    ensure_distinct_output_targets(args.output, args.report_output)
    for public_output in (args.output, args.report_output):
        if public_output.exists():
            raise X1ResumeTestbedError(f"x1_resume_public_output_exists:{public_output}")
    config_path = require_default_config_path(args.config, ROOT)
    config = X1ResumeConfig.from_path(config_path)
    source = source_identity()
    source_blobs = [
        git_blob_identity(relative, source["revision"]) for relative in REQUIRED_SOURCE_BLOB_PATHS
    ]
    manifest, samples = load_and_validate_repository(
        args.model_repository_root, config, args.data_root
    )
    if (
        manifest.get("source_revision") != source["revision"]
        or manifest.get("source_tree_sha") != source["tree_sha"]
        or manifest.get("source_blobs") != source_blobs
    ):
        raise X1ResumeTestbedError("x1_resume_repository_source_binding")
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
    assert_prometheus_preflight(prometheus_before)
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
    released_lease_evidence: dict[str, Any] | None = None
    released_lease_archive_evidence: dict[str, Any] | None = None
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
        validate_repository_entries(manifest, args.model_repository_root, config=config)
        active_containers.add(q0_name)
        q0_start = start_triton(
            config,
            args.model_repository_root / "batch-off",
            q0_root,
            q0_name,
            trace_enabled=True,
        )
        q0_readiness = wait_ready(config, q0_name)
        q0_configs = verify_model_configs(config, "off")
        q0_runtime_contract = write_runtime_contract(
            path=q0_root / "runtime-contract.json",
            suite_root=suite_root,
            profile="q0_isolated",
            batching="off",
            start_evidence=q0_start,
            readiness=q0_readiness,
            model_configs=q0_configs,
        )
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
            "runtime_contract": q0_runtime_contract,
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
            validate_repository_entries(manifest, args.model_repository_root, config=config)
            active_containers.add(name)
            profile_start = start_triton(
                config,
                args.model_repository_root / f"batch-{profile}",
                profile_root,
                name,
                trace_enabled=False,
            )
            profile_readiness = wait_ready(config, name)
            profile_configs = verify_model_configs(config, profile)
            profile_runtime_contract = write_runtime_contract(
                path=profile_root / "runtime-contract.json",
                suite_root=suite_root,
                profile=profile,
                batching=profile,
                start_evidence=profile_start,
                readiness=profile_readiness,
                model_configs=profile_configs,
            )
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
                    summaries.append(
                        run_cell(
                            config,
                            cell,
                            repetition,
                            samples,
                            attempt_root,
                            suite_id,
                        )
                    )
            stop_container(name)
            active_containers.discard(name)
            log_path = profile_root / "triton.log"
            profile_evidence[profile] = {
                "runtime_contract": profile_runtime_contract,
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
                released = release_scale_validation_gpu_lease(
                    run_id=lease.run_id,
                    lease_id=lease.lease_id,
                    fencing_token=lease.fencing_token,
                    reason=f"{suite_id} finished",
                )
                released_payload = released.model_dump(mode="json")
                archive_path = gpu_lease_root() / "history" / f"{lease.lease_id}.json"
                archive_raw = archive_path.read_bytes()
                if json.loads(archive_raw) != released_payload:
                    raise X1ResumeTestbedError("x1_resume_gpu_lease_archive_binding")
                released_path = suite_root / "gpu-lease-released.json"
                canonical_write(released_path, released_payload)
                archive_copy = suite_root / "gpu-lease-history-raw.json"
                archive_copy.write_bytes(archive_raw)
                released_lease_evidence = {
                    "path": released_path.relative_to(suite_root).as_posix(),
                    "bytes": released_path.stat().st_size,
                    "sha256": sha256_file(released_path),
                    "lease_id": released.lease_id,
                    "run_id": released.run_id,
                    "state": released.state,
                    "release_reason": released.release_reason,
                }
                released_lease_archive_evidence = {
                    "path": archive_copy.relative_to(suite_root).as_posix(),
                    "bytes": archive_copy.stat().st_size,
                    "sha256": sha256_file(archive_copy),
                }
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
        "gpu": capture_gpu,
        "triton_processes": capture_triton_processes,
        "containers": lambda: capture_container_state(expected_container_names),
        "ports": lambda: capture_port_state(config),
        "gpu_lease": capture_gpu_lease_state,
    }
    for key, operation in checks.items():
        try:
            final_checks[key] = operation()
        except Exception as exc:
            cleanup_errors.append(f"check:{key}:{type(exc).__name__}:{exc}")
    try:
        (
            prometheus_after,
            prometheus_restore_seconds,
            prometheus_restore_samples,
            prometheus_restore_ready,
            prometheus_restore_terminal_reason,
        ) = wait_prometheus_restore(config.cleanup_timeout_seconds)
        final_checks["prometheus"] = prometheus_after
        final_checks["prometheus_restore_seconds"] = prometheus_restore_seconds
        final_checks["prometheus_restore_samples"] = prometheus_restore_samples
        final_checks["prometheus_restore_ready"] = prometheus_restore_ready
        final_checks["prometheus_restore_terminal_reason"] = prometheus_restore_terminal_reason
    except Exception as exc:
        cleanup_errors.append(f"check:prometheus:{type(exc).__name__}:{exc}")
    try:
        gpu_after, vram_seconds = wait_vram_restore(gpu_before, config.cleanup_timeout_seconds)
        final_checks["gpu_after_vram_wait"] = gpu_after
        final_checks["vram_restore_seconds"] = vram_seconds
    except Exception as exc:
        cleanup_errors.append(f"check:vram:{type(exc).__name__}:{exc}")
    gpu_after = dict(final_checks.get("gpu_after_vram_wait", {}))
    vram_tolerance_mib = max(256.0, float(gpu_before["memory_total_mib"]) * 0.05)
    cleanup = {
        "container_absent": dict(final_checks.get("containers", {})).get("present_names") == [],
        "ports_absent": dict(final_checks.get("ports", {})).get("listening_ports") == [],
        "gpu_lease_absent": dict(final_checks.get("gpu_lease", {})).get("active") is None,
        "triton_gpu_process_residue": final_checks.get("triton_processes", []),
        "b0_identity_restored": final_checks.get("holder") == holder,
        "b0_cuda_restored": dict(final_checks.get("b0_cuda", {})).get("passed") is True,
        "queue_active_zero": dict(final_checks.get("queues", {})).get("active") == 0,
        "queue_leased_zero": dict(final_checks.get("queues", {})).get("leased") == 0,
        "queue_outcome_unknown_zero": dict(final_checks.get("queues", {})).get("outcome_unknown")
        == 0,
        "gpu_identity_restored": (gpu_after.get("uuid"), gpu_after.get("name"))
        == (config.expected_gpu_uuid, config.expected_gpu_name),
        "gpu_vram_restored": bool(gpu_after)
        and abs(
            float(gpu_after.get("memory_used_mib", float("inf")))
            - float(gpu_before["memory_used_mib"])
        )
        <= vram_tolerance_mib,
        "prometheus_5_of_5": final_checks.get("prometheus_restore_ready") is True
        and prometheus_baseline_ready(
            dict(final_checks.get("prometheus", {})), EXPECTED_PROMETHEUS_JOBS
        ),
        "prometheus_exact_jobs_restored": prometheus_baseline_ready(
            dict(final_checks.get("prometheus", {})), EXPECTED_PROMETHEUS_JOBS
        ),
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
                "gpu_identity_restored",
                "gpu_vram_restored",
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
        "source_blobs": source_blobs,
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
                "model_family": lease.model_family,
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
            "released_gpu_lease": released_lease_evidence,
            "released_gpu_lease_archive": released_lease_archive_evidence,
        },
        "claim_boundary": config.claim_boundary,
    }
    validate_evidence(
        public,
        config,
        private_suite_root=suite_root,
        model_repository_root=args.model_repository_root,
        source_root=ROOT,
        data_root=args.data_root,
    )
    canonical_write_once(args.output, public)
    report = generate_report(
        public,
        config,
        evidence_path=args.output,
        private_suite_root=suite_root,
        model_repository_root=args.model_repository_root,
        source_root=ROOT,
        data_root=args.data_root,
    )
    canonical_write_once(args.report_output, report)
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
