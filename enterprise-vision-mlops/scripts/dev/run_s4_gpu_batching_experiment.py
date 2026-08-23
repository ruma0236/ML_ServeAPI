from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import statistics
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
import numpy as np
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
from evm.scale_validation.evidence import write_public_json  # noqa: E402
from evm.scale_validation.s4_runtime import (  # noqa: E402
    CLAIM_BOUNDARY,
    S4Point,
    S4RuntimeConfig,
    S4RuntimeError,
    analyze_s4_results,
    canonical_sha256,
    file_sha256,
    source_identity,
    utc_now,
)


API_CONTAINER = "evm-s4-gpu-api"
PROMETHEUS_CONTAINER = "evm-s4-prom"
API_URL = "http://127.0.0.1:8014"
PROMETHEUS_URL = "http://127.0.0.1:9094"
REQUIRED_TRACE_SPANS = {
    "POST /control-panel/v1/scenario-workloads/gpu-batch-probes/predict",
    "s4.gpu_batch.worker",
}


@dataclass(frozen=True)
class HolderSnapshot:
    namespace: str
    name: str
    uid: str
    replicas: int
    available: int
    selector: str
    pod_uid: str
    pod_name: str
    image: str


@dataclass(frozen=True)
class RuntimeContext:
    image: str
    network: str
    source_revision: str
    source_branch: str
    registry_path: Path
    data_root: Path
    lease_run_id: str
    lease_id: str
    fencing_token: str
    private_root: Path
    trace_path: Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run S4 through the existing Workloads API and one CUDA device."
    )
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument(
        "--data-root",
        type=Path,
        default=Path("F:/EnterpriseMLOps_Data/enterprise-vision-mlops"),
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=ROOT / "configs" / "s4_gpu_batching_runtime.toml",
    )
    parser.add_argument(
        "--private-root",
        type=Path,
        default=Path(
            "F:/EnterpriseMLOps_Data/enterprise-vision-mlops/artifacts/scale_validation/private/s4"
        ),
    )
    parser.add_argument(
        "--trace-path",
        type=Path,
        default=Path(
            "F:/EnterpriseMLOps_Data/enterprise-vision-mlops/"
            "artifacts/scale_validation/otel/traces.json"
        ),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "docs/status/evidence/s4-gpu-batching-experiment.json",
    )
    parser.add_argument(
        "--smoke-output",
        type=Path,
        default=ROOT / "docs/status/evidence/s4-preparation-checkpoint.json",
    )
    parser.add_argument("--mode", choices=("smoke", "full"), default="smoke")
    parser.add_argument("--maintenance-approved", action="store_true")
    parser.add_argument("--reuse-image", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not args.maintenance_approved:
        raise S4RuntimeError("s4_exact_holder_handoff_requires_maintenance_approval")
    config = S4RuntimeConfig.from_path(args.config, data_root=args.data_root)
    revision, branch = source_identity(args.root)
    suite_id = f"{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}-{revision[:8]}"
    suite_root = args.private_root / suite_id
    suite_root.mkdir(parents=True, exist_ok=False)
    holder = capture_holder()
    gpu_before = capture_gpu_inventory()
    active_lease = read_active_gpu_lease()
    if active_lease is not None and active_lease.state == "active":
        raise S4RuntimeError(f"s4_gpu_lease_already_active:{active_lease.run_id}")
    serving_before = serving_readiness()
    if (
        serving_before.get("device") != "cuda"
        or serving_before.get("status") != "ok"
        or serving_before.get("model_loaded") is not True
    ):
        raise S4RuntimeError("s4_known_good_serving_preflight_failed")
    network = docker_network()
    image = f"enterprise-vision-mlops-gpu-batching:{revision[:12]}"
    if not args.reuse_image or not docker_image_exists(image):
        build_runtime_image(image, revision)
    failed_attempts: list[dict[str, Any]] = []
    cleanup: dict[str, Any] = {}
    inference_lease = None
    try:
        scale_holder(holder, replicas=0, require_ready=False)
        assert_holder_pods(holder, expected=0, require_ready=False)
        training_run_id = f"s4-training-{suite_id}"
        training_lease = acquire_scale_validation_gpu_lease(
            training_run_id,
            source_commit=revision,
            purpose="scale_validation_training",
            owner_pid=os.getpid(),
            ttl_seconds=3600,
        )
        try:
            training = train_model_in_container(
                image=image,
                network=network,
                data_root=args.data_root,
                config=args.config,
                source_revision=revision,
                lease=training_lease,
            )
            training["registry_path"] = str(config.model_root / "registry.json")
            training["artifact_path"] = str(config.model_root / "tiny-mlp.pt")
        finally:
            release_scale_validation_gpu_lease(
                run_id=training_lease.run_id,
                lease_id=training_lease.lease_id,
                fencing_token=training_lease.fencing_token,
                reason="S4 exclusive training window completed",
            )
        inference_run_id = f"s4-inference-{suite_id}"
        inference_lease = acquire_scale_validation_gpu_lease(
            inference_run_id,
            source_commit=revision,
            purpose="scale_validation_inference",
            owner_pid=os.getpid(),
            ttl_seconds=7200,
        )
        context = RuntimeContext(
            image=image,
            network=network,
            source_revision=revision,
            source_branch=branch,
            registry_path=Path(training["registry_path"]),
            data_root=args.data_root,
            lease_run_id=inference_lease.run_id,
            lease_id=inference_lease.lease_id,
            fencing_token=inference_lease.fencing_token,
            private_root=suite_root,
            trace_path=args.trace_path,
        )
        start_prometheus(context, config.prometheus_scrape_interval_seconds)
        if args.mode == "smoke":
            results = [
                run_point(
                    context=context,
                    config=config,
                    point=S4Point(1, 0, 1, "smoke"),
                    repetition=1,
                    warmup_seconds=2,
                    measurement_seconds=5,
                    cooldown_seconds=1,
                    open_rate=None,
                )
            ]
            public = preparation_checkpoint(
                config=config,
                revision=revision,
                branch=branch,
                holder=holder,
                gpu=gpu_before,
                training=training,
                result=results[0],
                private_root=suite_root,
            )
            write_public_json(args.smoke_output, public)
        else:
            results = []
            for point in config.matrix_points():
                for repetition in range(1, config.repetitions + 1):
                    results.append(
                        run_point(
                            context=context,
                            config=config,
                            point=point,
                            repetition=repetition,
                            warmup_seconds=config.warmup_seconds,
                            measurement_seconds=config.measurement_seconds,
                            cooldown_seconds=config.cooldown_seconds,
                            open_rate=None,
                        )
                    )
            point = config.instance_point()
            for repetition in range(1, config.repetitions + 1):
                results.append(
                    run_point(
                        context=context,
                        config=config,
                        point=point,
                        repetition=repetition,
                        warmup_seconds=config.warmup_seconds,
                        measurement_seconds=config.measurement_seconds,
                        cooldown_seconds=config.cooldown_seconds,
                        open_rate=None,
                    )
                )
            preliminary = analyze_s4_results(
                [
                    *results,
                    *[
                        {
                            **results[0],
                            "mode": "open-loop",
                            "evidence_valid": False,
                        }
                        for _ in range(0)
                    ],
                ],
                config,
            )
            selected = preliminary["selected_operating_point"]
            if selected is None:
                raise S4RuntimeError("s4_no_safe_operating_point")
            open_rate = float(selected["service_rps_mean"]) * config.open_service_rate_fraction
            selected_point = S4Point(
                int(selected["batch_size"]),
                int(selected["max_delay_ms"]),
                int(selected["instance_count"]),
                "open-loop",
            )
            for repetition in range(1, config.open_repetitions + 1):
                results.append(
                    run_point(
                        context=context,
                        config=config,
                        point=selected_point,
                        repetition=repetition,
                        warmup_seconds=config.warmup_seconds,
                        measurement_seconds=config.measurement_seconds,
                        cooldown_seconds=config.cooldown_seconds,
                        open_rate=open_rate,
                    )
                )
            analysis = analyze_s4_results(results, config)
            private_index = private_evidence_index(suite_root)
            canonical_write(suite_root / "private-evidence-index.json", private_index)
            public = {
                "schema_version": "evm.s4_gpu_batching_experiment.v1",
                "generated_at": utc_now(),
                "source_identity": {
                    "branch": branch,
                    "implementation_revision": revision,
                    "runtime_config_sha256": config.sha256,
                    "image": image,
                },
                "runtime_contract": config.public_dict(),
                "model_identity": {
                    "dataset_identity_sha256": training["dataset_identity_sha256"],
                    "split_manifest_sha256": training["split_manifest_sha256"],
                    "model_identity_sha256": training["model_identity_sha256"],
                    "artifact_sha256": training["artifact_sha256"],
                    "registry_sha256": training["registry_sha256"],
                    "architecture": config.architecture,
                    "framework": training["framework"],
                },
                "experiment_counts": {
                    "matrix": 60,
                    "instance_axis": 3,
                    "open_loop": 3,
                    "total": len(results),
                },
                "point_results": results,
                "analysis": analysis,
                "acceptance": analysis["acceptance"],
                "runtime_verdict": analysis["runtime_verdict"],
                "failed_attempts_and_rca": failed_attempts,
                "private_evidence": {
                    "artifact_count": private_index["artifact_count"],
                    "aggregate_sha256": private_index["aggregate_sha256"],
                    "total_bytes": private_index["total_bytes"],
                },
                "claim_boundary": CLAIM_BOUNDARY,
            }
            write_public_json(args.output, public)
            if not all(analysis["acceptance"].values()):
                raise S4RuntimeError(f"s4_acceptance_failed:{analysis['acceptance']}")
    except Exception as exc:
        failed = {
            "occurred_at": utc_now(),
            "failure": f"{type(exc).__name__}:{exc}",
            "action": "Stopped new load, retained private evidence, and entered exact cleanup.",
        }
        failed_attempts.append(failed)
        canonical_write(suite_root / "failed-attempt.json", failed)
        raise
    finally:
        stop_container(API_CONTAINER)
        stop_container(PROMETHEUS_CONTAINER)
        if inference_lease is not None:
            try:
                release_scale_validation_gpu_lease(
                    run_id=inference_lease.run_id,
                    lease_id=inference_lease.lease_id,
                    fencing_token=inference_lease.fencing_token,
                    reason="S4 inference window completed or stopped",
                )
                cleanup["lease_released"] = True
            except Exception as exc:
                cleanup["lease_released"] = False
                cleanup["lease_release_error"] = f"{type(exc).__name__}:{exc}"
        try:
            scale_holder(holder, replicas=holder.replicas, require_ready=True)
            restored = capture_holder()
            cleanup["holder_uid_restored"] = restored.uid == holder.uid
            cleanup["holder_ready"] = restored.available == holder.replicas
            ready = serving_readiness(timeout=30)
            cleanup["serving_cuda_ready"] = (
                ready.get("status") == "ok"
                and ready.get("model_loaded") is True
                and ready.get("device") == "cuda"
            )
        except Exception as exc:
            cleanup["holder_restore_error"] = f"{type(exc).__name__}:{exc}"
        cleanup["active_lease_absent"] = read_active_gpu_lease() is None
        cleanup["api_container_absent"] = not container_exists(API_CONTAINER)
        cleanup["prometheus_container_absent"] = not container_exists(PROMETHEUS_CONTAINER)
        cleanup["gpu_after"] = capture_gpu_inventory()
        canonical_write(suite_root / "cleanup.json", cleanup)
        if not all(
            cleanup.get(key) is True
            for key in (
                "lease_released",
                "holder_uid_restored",
                "holder_ready",
                "serving_cuda_ready",
                "active_lease_absent",
                "api_container_absent",
                "prometheus_container_absent",
            )
        ):
            raise S4RuntimeError(f"s4_cleanup_incomplete:{cleanup}")
    print(
        json.dumps(
            {
                "mode": args.mode,
                "source_revision": revision,
                "suite_root": str(suite_root),
                "output": str(args.smoke_output if args.mode == "smoke" else args.output),
                "cleanup": cleanup,
            },
            sort_keys=True,
        )
    )
    return 0


def build_runtime_image(image: str, revision: str) -> None:
    run_checked(
        [
            "docker",
            "build",
            "--build-arg",
            f"SOURCE_REVISION={revision}",
            "-f",
            "infra/docker/gpu-batching/Dockerfile",
            "-t",
            image,
            ".",
        ],
        timeout=1800,
    )


def train_model_in_container(
    *,
    image: str,
    network: str,
    data_root: Path,
    config: Path,
    source_revision: str,
    lease: Any,
) -> dict[str, Any]:
    config_inside = f"/app/configs/{config.name}"
    command = [
        "docker",
        "run",
        "--rm",
        "--gpus",
        "all",
        "--network",
        network,
        "-v",
        f"{data_root}:/mnt/evm-data",
        "-e",
        "EVM_SCENARIO_GPU_LEASE_ROOT=/mnt/evm-data/runtime/gpu-lease",
        image,
        "python",
        "/app/scripts/dev/prepare_s4_tiny_mlp.py",
        "--root",
        "/app",
        "--data-root",
        "/mnt/evm-data",
        "--config",
        config_inside,
        "--source-revision",
        source_revision,
        "--lease-run-id",
        lease.run_id,
        "--lease-id",
        lease.lease_id,
        "--fencing-token",
        lease.fencing_token,
    ]
    completed = run_checked(command, timeout=1800)
    for line in reversed(completed.stdout.splitlines()):
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict) and payload.get("model_identity_sha256"):
            return payload
    raise S4RuntimeError("s4_training_result_missing")


def run_point(
    *,
    context: RuntimeContext,
    config: S4RuntimeConfig,
    point: S4Point,
    repetition: int,
    warmup_seconds: float,
    measurement_seconds: float,
    cooldown_seconds: float,
    open_rate: float | None,
) -> dict[str, Any]:
    config.assert_frozen()
    point_root = context.private_root / point.point_id / f"repetition-{repetition}"
    point_root.mkdir(parents=True, exist_ok=False)
    trace_offset = context.trace_path.stat().st_size if context.trace_path.is_file() else 0
    start_gpu_api(context, config, point)
    try:
        descriptor = requests.get(
            f"{API_URL}/control-panel/v1/scenario-workloads/gpu-batch-probes", timeout=10
        )
        descriptor.raise_for_status()
        identity = descriptor.json()
        if identity.get("architecture") != config.architecture:
            raise S4RuntimeError("s4_runtime_descriptor_identity_mismatch")
        wait_prometheus_up()
        features = np.load(config.replay_features_path, mmap_mode="r")
        asyncio.run(
            execute_load_phase(
                features=features,
                identity=identity,
                run_id=f"warmup-{point.point_id}-{repetition}",
                duration_seconds=warmup_seconds,
                concurrency=min(16, config.closed_concurrency),
                open_rate=None,
                record=False,
                seed=config.seed + repetition,
                request_timeout_seconds=config.request_timeout_seconds,
            )
        )
        measurement = asyncio.run(
            measured_phase(
                features=features,
                identity=identity,
                run_id=f"s4-{point.point_id}-{repetition}-{uuid4().hex[:8]}",
                duration_seconds=measurement_seconds,
                concurrency=config.closed_concurrency,
                open_rate=open_rate,
                seed=config.seed + repetition * 1000 + point.batch_size * 10 + point.max_delay_ms,
                request_timeout_seconds=config.request_timeout_seconds,
                sample_interval_seconds=config.resource_sample_interval_seconds,
            )
        )
        time.sleep(cooldown_seconds)
        drain = wait_runtime_drain(config.queue_drain_timeout_seconds)
        time.sleep(2)
        trace = trace_summary(
            context.trace_path,
            offset=trace_offset,
            expected=set(measurement["expected_sampled_trace_ids"]),
        )
        result = summarize_point(
            point=point,
            repetition=repetition,
            measurement=measurement,
            drain=drain,
            trace=trace,
            config=config,
        )
        private_payload = {
            "schema_version": "evm.s4_gpu_batch_point_private.v1",
            "generated_at": utc_now(),
            "point": point.__dict__,
            "repetition": repetition,
            "source_revision": context.source_revision,
            "runtime_config_sha256": config.sha256,
            "measurement": measurement,
            "drain": drain,
            "trace": trace,
            "summary": result,
        }
        canonical_write(point_root / "point-private.json", private_payload)
        result["private_point_sha256"] = file_sha256(point_root / "point-private.json")
        return result
    finally:
        stop_container(API_CONTAINER)


async def measured_phase(**kwargs: Any) -> dict[str, Any]:
    stop = asyncio.Event()
    resources: list[dict[str, Any]] = []
    sampler = asyncio.create_task(
        sample_resources(
            stop,
            resources,
            float(kwargs.pop("sample_interval_seconds")),
        )
    )
    try:
        result = await execute_load_phase(record=True, **kwargs)
    finally:
        stop.set()
        await sampler
    result["resource_samples"] = resources
    return result


async def execute_load_phase(
    *,
    features: np.ndarray,
    identity: dict[str, Any],
    run_id: str,
    duration_seconds: float,
    concurrency: int,
    open_rate: float | None,
    record: bool,
    seed: int,
    request_timeout_seconds: float,
) -> dict[str, Any]:
    deadline = time.perf_counter() + duration_seconds
    lock = asyncio.Lock()
    latencies: list[float] = []
    queue_waits: list[float] = []
    formed_batches: list[int] = []
    h2d: list[float] = []
    inference: list[float] = []
    d2h: list[float] = []
    allocated: list[int] = []
    reserved: list[int] = []
    peak: list[int] = []
    statuses: dict[str, int] = {}
    expected_traces: list[str] = []
    trace_matches = 0
    request_index = 0
    rng = np.random.default_rng(seed)
    order = rng.integers(0, len(features), size=max(100_000, concurrency * 100), endpoint=False)
    limits = httpx.Limits(max_connections=max(256, concurrency * 2), max_keepalive_connections=256)
    timeout = httpx.Timeout(request_timeout_seconds)

    async with httpx.AsyncClient(timeout=timeout, limits=limits) as client:

        async def one(index: int) -> None:
            nonlocal trace_matches
            feature_index = int(order[index % len(order)])
            traceparent, trace_id, sampled = deterministic_traceparent(run_id, index)
            payload = {
                "schema_version": "evm.s4_gpu_batch_request.v1",
                "dataset_identity_sha256": identity["dataset_identity_sha256"],
                "model_identity_sha256": identity["model_identity_sha256"],
                "features": [float(value) for value in features[feature_index]],
            }
            started = time.perf_counter()
            try:
                response = await client.post(
                    f"{API_URL}/control-panel/v1/scenario-workloads/gpu-batch-probes/predict",
                    json=payload,
                    headers={"traceparent": traceparent},
                )
                elapsed_ms = (time.perf_counter() - started) * 1000
                body = response.json() if response.content else {}
                if response.status_code == 500 and "out of memory" in response.text.lower():
                    status = "500-oom"
                else:
                    status = str(response.status_code)
                response_trace = response.headers.get("x-evm-trace-id")
            except Exception:
                elapsed_ms = (time.perf_counter() - started) * 1000
                status = "transport-error"
                body = {}
                response_trace = None
            async with lock:
                statuses[status] = statuses.get(status, 0) + 1
                if sampled:
                    expected_traces.append(trace_id)
                if response_trace == trace_id:
                    trace_matches += 1
                if record:
                    latencies.append(elapsed_ms)
                    if status == "200":
                        timings = body["timings"]
                        runtime = body["runtime"]
                        queue_waits.append(float(timings["queue_wait_ms"]))
                        formed_batches.append(int(runtime["formed_batch_size"]))
                        h2d.append(float(timings["h2d_ms"]))
                        inference.append(float(timings["inference_ms"]))
                        d2h.append(float(timings["d2h_ms"]))
                        allocated.append(int(runtime["allocated_vram_bytes"]))
                        reserved.append(int(runtime["reserved_vram_bytes"]))
                        peak.append(int(runtime["peak_vram_bytes"]))

        if open_rate is None:

            async def closed_worker(worker_id: int) -> None:
                nonlocal request_index
                while time.perf_counter() < deadline:
                    async with lock:
                        index = request_index
                        request_index += 1
                    await one(index + worker_id * 10_000_000)

            await asyncio.gather(*(closed_worker(worker) for worker in range(concurrency)))
        else:
            tasks: set[asyncio.Task[None]] = set()
            interval = 1 / max(open_rate, 1e-9)
            next_release = time.perf_counter()
            while time.perf_counter() < deadline:
                now = time.perf_counter()
                if now < next_release:
                    await asyncio.sleep(next_release - now)
                index = request_index
                request_index += 1
                task = asyncio.create_task(one(index))
                tasks.add(task)
                task.add_done_callback(tasks.discard)
                if len(tasks) >= concurrency * 4:
                    await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                next_release += interval
            if tasks:
                await asyncio.gather(*tasks)
    return {
        "duration_seconds": duration_seconds,
        "offered_rate": open_rate,
        "request_count": len(latencies) if record else sum(statuses.values()),
        "statuses": statuses,
        "latencies_ms": latencies,
        "queue_waits_ms": queue_waits,
        "formed_batch_sizes": formed_batches,
        "h2d_ms": h2d,
        "inference_ms": inference,
        "d2h_ms": d2h,
        "allocated_vram_bytes": allocated,
        "reserved_vram_bytes": reserved,
        "peak_vram_bytes": peak,
        "expected_sampled_trace_ids": sorted(set(expected_traces)),
        "response_trace_identity_matches": trace_matches,
    }


async def sample_resources(
    stop: asyncio.Event, samples: list[dict[str, Any]], interval: float
) -> None:
    while not stop.is_set():
        sample = await asyncio.to_thread(resource_sample)
        samples.append(sample)
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval)
        except TimeoutError:
            pass


def resource_sample() -> dict[str, Any]:
    gpu = capture_gpu_inventory()
    stats = run_checked(
        [
            "docker",
            "stats",
            "--no-stream",
            "--format",
            "{{json .}}",
            API_CONTAINER,
        ],
        timeout=10,
    )
    payload = json.loads(stats.stdout.strip())
    return {
        "captured_at": utc_now(),
        "gpu_utilization_percent": gpu["utilization_gpu_percent"],
        "temperature_celsius": gpu["temperature_celsius"],
        "power_watts": gpu["power_watts"],
        "memory_used_mib": gpu["memory_used_mib"],
        "memory_free_mib": gpu["memory_free_mib"],
        "container_memory_usage": payload.get("MemUsage"),
        "container_cpu_percent": payload.get("CPUPerc"),
    }


def summarize_point(
    *,
    point: S4Point,
    repetition: int,
    measurement: dict[str, Any],
    drain: dict[str, float],
    trace: dict[str, Any],
    config: S4RuntimeConfig,
) -> dict[str, Any]:
    latencies = measurement["latencies_ms"]
    successes = int(measurement["statuses"].get("200", 0))
    request_count = len(latencies)
    errors = request_count - successes
    resources = measurement["resource_samples"]
    formed = measurement["formed_batch_sizes"]
    oom = sum(count for status, count in measurement["statuses"].items() if status == "500-oom")
    result = {
        "point_id": point.point_id,
        "mode": point.mode,
        "repetition": repetition,
        "batch_size": point.batch_size,
        "max_delay_ms": point.max_delay_ms,
        "instance_count": point.instance_count,
        "request_count": request_count,
        "success_count": successes,
        "service_rps": successes / float(measurement["duration_seconds"]),
        "p50_ms": percentile(latencies, 50),
        "p95_ms": percentile(latencies, 95),
        "p99_ms": percentile(latencies, 99),
        "error_rate": errors / max(1, request_count),
        "queue_wait_p99_ms": percentile(measurement["queue_waits_ms"], 99),
        "h2d_ms_mean": mean(measurement["h2d_ms"]),
        "inference_ms_mean": mean(measurement["inference_ms"]),
        "d2h_ms_mean": mean(measurement["d2h_ms"]),
        "formed_batch_size_mean": mean(formed),
        "fill_ratio_mean": mean(formed) / point.batch_size,
        "formed_batch_distribution": frequency(formed),
        "allocated_vram_bytes_max": max(measurement["allocated_vram_bytes"], default=0),
        "reserved_vram_bytes_max": max(measurement["reserved_vram_bytes"], default=0),
        "peak_vram_bytes": max(measurement["peak_vram_bytes"], default=0),
        "gpu_utilization_percent_mean": mean(
            [item["gpu_utilization_percent"] for item in resources]
        ),
        "temperature_celsius_max": max(
            [item["temperature_celsius"] for item in resources], default=0
        ),
        "power_watts_max": max([item["power_watts"] for item in resources], default=0),
        "oom_count": oom,
        "prometheus_up": prometheus_scalar('up{job="evm-s4-gpu-batch"}') == 1,
        "terminal_gauges": drain,
        "trace": trace,
    }
    result["evidence_valid"] = bool(
        request_count > 0
        and successes > 0
        and result["error_rate"] <= config.maximum_error_rate
        and result["p99_ms"] <= config.maximum_p99_ms
        and result["queue_wait_p99_ms"] <= config.maximum_queue_wait_ms
        and result["temperature_celsius_max"] <= config.maximum_temperature_celsius
        and result["power_watts_max"] <= config.maximum_power_watts
        and result["oom_count"] == 0
        and result["prometheus_up"]
        and all(value == 0 for value in drain.values())
        and trace["missing_count"] == 0
    )
    if not result["evidence_valid"]:
        raise S4RuntimeError(f"s4_point_guardrail_failed:{result}")
    return result


def build_gpu_api_command(
    context: RuntimeContext, config: S4RuntimeConfig, point: S4Point
) -> list[str]:
    registry_inside = (
        "/mnt/evm-data/" + context.registry_path.relative_to(context.data_root).as_posix()
    )
    return [
        "docker",
        "run",
        "-d",
        "--name",
        API_CONTAINER,
        "--gpus",
        "all",
        "--network",
        context.network,
        "-p",
        "127.0.0.1:8014:8000",
        "-v",
        f"{context.data_root}:/mnt/evm-data:ro",
        "-e",
        "APP_NAME=enterprise-vision-mlops-s4-gpu-api",
        "-e",
        "EVM_CONTROL_PLANE_STORE_MODE=file",
        "-e",
        "EVM_TASK_ADMISSION_MODE=legacy",
        "-e",
        "EVM_CONTROL_PANEL_LEDGER_ROOT=/tmp/operations",
        "-e",
        "EVM_SCENARIO_WORKLOAD_ROOT=/tmp/scenario-workloads",
        "-e",
        "EVM_PIPELINE_PROFILE_ROOT=/tmp/pipeline-profiles",
        "-e",
        "EVM_LIFECYCLE_RUN_ROOT=/tmp/lifecycle-runs",
        "-e",
        "EVM_EXPERIMENT_RUN_ROOT=/tmp/experiment-runs",
        "-e",
        "EVM_MODEL_COMPONENT_REGISTRY_ROOT=/tmp/model-components",
        "-e",
        "EVM_SCENARIO_GPU_LEASE_ROOT=/mnt/evm-data/runtime/gpu-lease",
        "-e",
        "EVM_S4_GPU_BATCH_ENABLED=true",
        "-e",
        f"EVM_S4_GPU_BATCH_REGISTRY={registry_inside}",
        "-e",
        f"EVM_S4_GPU_BATCH_SIZE={point.batch_size}",
        "-e",
        f"EVM_S4_GPU_BATCH_MAX_DELAY_MS={point.max_delay_ms}",
        "-e",
        f"EVM_S4_GPU_INSTANCE_COUNT={point.instance_count}",
        "-e",
        f"EVM_S4_GPU_MAX_OUTSTANDING={config.max_outstanding}",
        "-e",
        f"EVM_S4_GPU_MAX_OUTSTANDING_BYTES={config.max_outstanding_bytes}",
        "-e",
        f"EVM_S4_GPU_MAX_REQUEST_BYTES={config.max_request_bytes}",
        "-e",
        f"EVM_S4_GPU_ADMISSION_WAIT_SECONDS={config.admission_wait_seconds}",
        "-e",
        f"EVM_S4_GPU_REQUEST_TIMEOUT_SECONDS={config.request_timeout_seconds}",
        "-e",
        f"EVM_S4_GPU_RETRY_AFTER_SECONDS={config.retry_after_seconds}",
        "-e",
        f"EVM_S4_GPU_LEASE_RUN_ID={context.lease_run_id}",
        "-e",
        f"EVM_S4_GPU_LEASE_ID={context.lease_id}",
        "-e",
        f"EVM_S4_GPU_LEASE_FENCING_TOKEN={context.fencing_token}",
        "-e",
        f"GIT_COMMIT={context.source_revision}",
        "-e",
        f"GIT_BRANCH={context.source_branch}",
        "-e",
        "EVM_OTEL_ENABLED=true",
        "-e",
        "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT=http://evm-otel-collector:4318/v1/traces",
        "-e",
        "OTEL_SERVICE_NAMESPACE=enterprise-mlops",
        "-e",
        "OTEL_SERVICE_INSTANCE_ID=s4-gpu-api",
        "-e",
        "OTEL_TRACES_SAMPLER=parentbased_traceidratio",
        "-e",
        "OTEL_TRACES_SAMPLER_ARG=0.001",
        context.image,
    ]


def start_gpu_api(context: RuntimeContext, config: S4RuntimeConfig, point: S4Point) -> None:
    stop_container(API_CONTAINER)
    command = build_gpu_api_command(context, config, point)
    run_checked(command, timeout=60)
    wait_http(f"{API_URL}/health", timeout=120)


def start_prometheus(context: RuntimeContext, scrape_interval: float) -> None:
    stop_container(PROMETHEUS_CONTAINER)
    config_path = context.private_root / "prometheus.yml"
    config_path.write_text(
        "\n".join(
            [
                "global:",
                f"  scrape_interval: {scrape_interval:g}s",
                "scrape_configs:",
                '  - job_name: "evm-s4-gpu-batch"',
                "    metrics_path: /metrics",
                "    static_configs:",
                f'      - targets: ["{API_CONTAINER}:8000"]',
                "",
            ]
        ),
        encoding="utf-8",
        newline="\n",
    )
    run_checked(
        [
            "docker",
            "run",
            "-d",
            "--rm",
            "--name",
            PROMETHEUS_CONTAINER,
            "--network",
            context.network,
            "-p",
            "127.0.0.1:9094:9090",
            "-v",
            f"{config_path}:/etc/prometheus/prometheus.yml:ro",
            "prom/prometheus:v2.55.1",
            "--config.file=/etc/prometheus/prometheus.yml",
            "--storage.tsdb.path=/prometheus",
        ],
        timeout=60,
    )
    wait_http(
        f"{PROMETHEUS_URL}/-/ready",
        timeout=60,
        logs_container=PROMETHEUS_CONTAINER,
    )


def wait_prometheus_up(timeout: float = 30) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if prometheus_scalar('up{job="evm-s4-gpu-batch"}') == 1:
                return
        except Exception:
            pass
        time.sleep(1)
    raise S4RuntimeError("s4_prometheus_target_not_up")


def wait_runtime_drain(timeout: float) -> dict[str, float]:
    metrics = (
        "evm_s4_gpu_batch_queue_depth",
        "evm_s4_gpu_batch_queue_bytes",
        "evm_s4_gpu_batch_in_flight",
    )
    deadline = time.monotonic() + timeout
    last: dict[str, float] = {}
    while time.monotonic() < deadline:
        last = {metric: prometheus_scalar(f"sum({metric})") for metric in metrics}
        if all(value == 0 for value in last.values()):
            return last
        time.sleep(0.25)
    raise S4RuntimeError(f"s4_gpu_queue_drain_timeout:{last}")


def prometheus_scalar(query: str) -> float:
    response = requests.get(f"{PROMETHEUS_URL}/api/v1/query", params={"query": query}, timeout=5)
    response.raise_for_status()
    result = response.json()["data"]["result"]
    return 0.0 if not result else float(result[0]["value"][1])


def trace_summary(path: Path, *, offset: int, expected: set[str]) -> dict[str, Any]:
    deadline = time.monotonic() + 10
    by_trace: dict[str, set[str]] = {trace_id: set() for trace_id in expected}
    raw = b""
    while True:
        if path.is_file():
            with path.open("rb") as handle:
                handle.seek(offset)
                raw = handle.read()
        for line in raw.splitlines():
            try:
                payload = json.loads(line)
            except (UnicodeDecodeError, json.JSONDecodeError):
                continue
            for resource in payload.get("resourceSpans", []):
                for scope in resource.get("scopeSpans", []):
                    for span in scope.get("spans", []):
                        trace_id = str(span.get("traceId") or "")
                        if trace_id in by_trace:
                            by_trace[trace_id].add(str(span.get("name") or ""))
        complete = {
            trace_id for trace_id, names in by_trace.items() if REQUIRED_TRACE_SPANS.issubset(names)
        }
        if len(complete) == len(expected) or time.monotonic() >= deadline:
            break
        time.sleep(0.5)
    return {
        "expected_count": len(expected),
        "complete_count": len(complete),
        "missing_count": len(expected - complete),
        "required_span_names": sorted(REQUIRED_TRACE_SPANS),
        "observed_span_names": sorted({name for names in by_trace.values() for name in names}),
        "raw_tail_sha256": hashlib.sha256(raw).hexdigest(),
        "raw_tail_bytes": len(raw),
    }


def deterministic_traceparent(run_id: str, index: int) -> tuple[str, str, bool]:
    trace_id = hashlib.sha256(f"{run_id}:trace:{index}".encode()).hexdigest()[:32]
    span_id = hashlib.sha256(f"{run_id}:span:{index}".encode()).hexdigest()[:16]
    sampled = index % 500 == 0
    return f"00-{trace_id}-{span_id}-{'01' if sampled else '00'}", trace_id, sampled


def preparation_checkpoint(
    *,
    config: S4RuntimeConfig,
    revision: str,
    branch: str,
    holder: HolderSnapshot,
    gpu: dict[str, Any],
    training: dict[str, Any],
    result: dict[str, Any],
    private_root: Path,
) -> dict[str, Any]:
    private_index = private_evidence_index(private_root)
    return {
        "schema_version": "evm.s4_preparation_checkpoint.v1",
        "generated_at": utc_now(),
        "status": "implementing",
        "acceptance_credit": False,
        "source_identity": {
            "branch": branch,
            "implementation_revision": revision,
            "runtime_config_sha256": config.sha256,
        },
        "existing_system_path": (
            "external client -> existing Workloads API route -> bounded dynamic GPU "
            "batcher -> governed Tiny MLP artifact -> Prometheus/OTLP"
        ),
        "gpu_preflight": {
            "device_count": 1,
            "device_name": gpu["name"],
            "driver": gpu["driver"],
            "total_memory_mib": gpu["memory_total_mib"],
            "holder_target_kind": "Deployment",
            "holder_target_uid_bound": bool(holder.uid),
        },
        "model": {
            "architecture": config.architecture,
            "artifact_sha256": training["artifact_sha256"],
            "model_identity_sha256": training["model_identity_sha256"],
            "dataset_identity_sha256": training["dataset_identity_sha256"],
            "training": training["training"],
            "framework": training["framework"],
        },
        "batch_one_smoke": result,
        "private_evidence": {
            "artifact_count": private_index["artifact_count"],
            "aggregate_sha256": private_index["aggregate_sha256"],
            "total_bytes": private_index["total_bytes"],
        },
        "acceptance": {f"S4-AC-0{index}": "pending" for index in range(1, 5)},
        "next_action": "Run the frozen 60+3+3 controlled matrix at a clean revision.",
        "claim_boundary": CLAIM_BOUNDARY,
    }


def capture_holder() -> HolderSnapshot:
    namespace = "evm-production"
    name = "evm-b0-production"
    deployment = kubectl_json(
        ["kubectl", "-n", namespace, "get", f"deployment/{name}", "-o", "json"]
    )
    replicas = int(deployment.get("spec", {}).get("replicas") or 0)
    available = int(deployment.get("status", {}).get("availableReplicas") or 0)
    uid = str(deployment.get("metadata", {}).get("uid") or "")
    selector_map = deployment.get("spec", {}).get("selector", {}).get("matchLabels", {})
    selector = ",".join(f"{key}={value}" for key, value in sorted(selector_map.items()))
    pods = kubectl_json(["kubectl", "-n", namespace, "get", "pods", "-l", selector, "-o", "json"])
    active = [
        item
        for item in pods.get("items", [])
        if not item.get("metadata", {}).get("deletionTimestamp")
        and item.get("status", {}).get("phase") == "Running"
    ]
    if not uid or replicas != 1 or available != 1 or len(active) != 1:
        raise S4RuntimeError("s4_holder_identity_not_exact_ready")
    return HolderSnapshot(
        namespace=namespace,
        name=name,
        uid=uid,
        replicas=replicas,
        available=available,
        selector=selector,
        pod_uid=str(active[0].get("metadata", {}).get("uid") or ""),
        pod_name=str(active[0].get("metadata", {}).get("name") or ""),
        image=str(deployment["spec"]["template"]["spec"]["containers"][0]["image"]),
    )


def scale_holder(holder: HolderSnapshot, *, replicas: int, require_ready: bool) -> None:
    current = kubectl_json(
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
    if str(current.get("metadata", {}).get("uid") or "") != holder.uid:
        raise S4RuntimeError("s4_holder_uid_changed")
    run_checked(
        [
            "kubectl",
            "-n",
            holder.namespace,
            "scale",
            f"deployment/{holder.name}",
            f"--replicas={replicas}",
        ],
        timeout=60,
    )
    assert_holder_pods(holder, expected=replicas, require_ready=require_ready)


def assert_holder_pods(
    holder: HolderSnapshot, *, expected: int, require_ready: bool, timeout: float = 300
) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        pods = kubectl_json(
            [
                "kubectl",
                "-n",
                holder.namespace,
                "get",
                "pods",
                "-l",
                holder.selector,
                "-o",
                "json",
            ]
        )
        active = [
            item
            for item in pods.get("items", [])
            if not item.get("metadata", {}).get("deletionTimestamp")
            and item.get("status", {}).get("phase") in {"Pending", "Running"}
        ]
        ready = [
            item
            for item in active
            if any(
                condition.get("type") == "Ready" and condition.get("status") == "True"
                for condition in item.get("status", {}).get("conditions", [])
            )
        ]
        if len(active) == expected and (not require_ready or len(ready) == expected):
            return
        time.sleep(2)
    raise S4RuntimeError(f"s4_holder_pod_wait_timeout:expected={expected}:ready={require_ready}")


def serving_readiness(timeout: float = 10) -> dict[str, Any]:
    service = kubectl_json(
        [
            "kubectl",
            "-n",
            "evm-production",
            "get",
            "service/evm-b0-production",
            "-o",
            "json",
        ]
    )
    ports = service.get("spec", {}).get("ports", [])
    node_ports = [int(item["nodePort"]) for item in ports if item.get("nodePort")]
    if len(node_ports) != 1:
        raise S4RuntimeError(f"s4_holder_node_port_ambiguous:{len(node_ports)}")
    response = requests.get(f"http://127.0.0.1:{node_ports[0]}/ready", timeout=timeout)
    response.raise_for_status()
    return response.json()


def capture_gpu_inventory() -> dict[str, Any]:
    result = run_checked(
        [
            "nvidia-smi",
            "--query-gpu=uuid,name,driver_version,memory.total,memory.used,memory.free,"
            "utilization.gpu,temperature.gpu,power.draw",
            "--format=csv,noheader,nounits",
        ],
        timeout=10,
    )
    lines = [line for line in result.stdout.splitlines() if line.strip()]
    if len(lines) != 1:
        raise S4RuntimeError(f"s4_requires_one_gpu:{len(lines)}")
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


def private_evidence_index(root: Path) -> dict[str, Any]:
    entries = []
    total = 0
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        relative = path.relative_to(root).as_posix()
        size = path.stat().st_size
        digest = file_sha256(path)
        entries.append({"path": relative, "size_bytes": size, "sha256": digest})
        total += size
    return {
        "schema_version": "evm.s4_private_evidence_index.v1",
        "generated_at": utc_now(),
        "artifact_count": len(entries),
        "total_bytes": total,
        "aggregate_sha256": canonical_sha256(entries),
        "entries": entries,
    }


def frequency(values: list[int]) -> dict[str, int]:
    result: dict[str, int] = {}
    for value in values:
        key = str(value)
        result[key] = result.get(key, 0) + 1
    return dict(sorted(result.items(), key=lambda item: int(item[0])))


def percentile(values: list[float], value: float) -> float:
    if not values:
        return 0.0
    return float(np.percentile(np.asarray(values, dtype="float64"), value))


def mean(values: list[float | int]) -> float:
    return 0.0 if not values else float(statistics.fmean(values))


def canonical_write(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(
        (json.dumps(payload, sort_keys=True, indent=2, allow_nan=False) + "\n").encode("utf-8")
    )


def kubectl_json(command: list[str]) -> dict[str, Any]:
    completed = run_checked(command, timeout=30)
    payload = json.loads(completed.stdout)
    if not isinstance(payload, dict):
        raise S4RuntimeError("s4_kubectl_payload_invalid")
    return payload


def docker_network() -> str:
    output = run_checked(
        [
            "docker",
            "inspect",
            "evm-api",
            "--format",
            "{{range $k,$v := .NetworkSettings.Networks}}{{$k}}{{end}}",
        ],
        timeout=15,
    ).stdout.strip()
    if not output:
        raise S4RuntimeError("s4_docker_network_missing")
    return output


def docker_image_exists(image: str) -> bool:
    return (
        subprocess.run(
            ["docker", "image", "inspect", image],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        ).returncode
        == 0
    )


def container_exists(name: str) -> bool:
    return (
        subprocess.run(
            ["docker", "inspect", name],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        ).returncode
        == 0
    )


def stop_container(name: str) -> None:
    if container_exists(name):
        subprocess.run(
            ["docker", "rm", "-f", name],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )


def wait_http(url: str, *, timeout: float, logs_container: str = API_CONTAINER) -> None:
    deadline = time.monotonic() + timeout
    last = ""
    while time.monotonic() < deadline:
        try:
            response = requests.get(url, timeout=2)
            if response.status_code == 200:
                return
            last = f"status={response.status_code}"
        except requests.RequestException as exc:
            last = str(exc)
        time.sleep(1)
    logs = subprocess.run(
        ["docker", "logs", "--tail", "100", logs_container],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    raise S4RuntimeError(f"s4_http_wait_timeout:{url}:{last}:{logs.stderr}:{logs.stdout}")


def run_checked(command: list[str], *, timeout: float) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        command,
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=False,
    )
    if completed.returncode != 0:
        raise S4RuntimeError(
            f"s4_command_failed:{' '.join(command[:5])}:"
            f"stdout={completed.stdout[-2000:]}:stderr={completed.stderr[-2000:]}"
        )
    return completed


if __name__ == "__main__":
    raise SystemExit(main())
