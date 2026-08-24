from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
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
from evm.scale_validation.e0_runtime import (  # noqa: E402
    E0RuntimeConfig,
    canonical,
    sha256_file,
)

from run_s8_v4_e0_environment import (  # noqa: E402
    b0_cuda_inference,
    capture_environment,
    capture_gpu,
    capture_holder,
    container_exists,
    container_gpu,
    infer,
    metric_value,
    ports_absent,
    prometheus_baseline,
    prometheus_query,
    queue_counts,
    remove_target,
    scale_holder,
    source_identity,
    start_triton,
    stop_triton,
    target_health,
    temporary_kubernetes_resources_absent,
    wait_http_ready,
    wait_target,
    wait_vram_restore,
    write_target,
)


CONTAINER = "evm-s8-v4-e0-remediation-smoke"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run one non-credit E0 remediation smoke.")
    parser.add_argument(
        "--config",
        type=Path,
        default=ROOT / "configs/s8_v4_e0_environment_v1.toml",
    )
    parser.add_argument("--model-repository", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    return parser.parse_args()


def write_json(path: Path, payload: object) -> None:
    path.write_text(canonical(payload) + "\n", encoding="utf-8", newline="\n")


def main() -> int:
    args = parse_args()
    if args.output_root.exists():
        raise RuntimeError("e0_remediation_smoke_output_exists")
    args.output_root.mkdir(parents=True)
    config = E0RuntimeConfig.from_path(args.config)
    branch, revision, tree = source_identity()
    manifest_path = args.model_repository / "model-repository-manifest.json"
    manifest = json.loads(manifest_path.read_bytes())
    artifact = args.model_repository / config.model_name / config.model_version / "model.pt"
    model_config = args.model_repository / config.model_name / "config.pbtxt"
    if (
        manifest.get("artifact_sha256") != sha256_file(artifact)
        or manifest.get("config_sha256") != sha256_file(model_config)
        or manifest.get("model_name") != config.model_name
        or manifest.get("model_version") != config.model_version
    ):
        raise RuntimeError("e0_remediation_smoke_model_identity")
    attempt_id = f"e0-remediation-smoke-{uuid4().hex[:12]}"
    holder = capture_holder()
    gpu_before = capture_gpu()
    queues_before = queue_counts()
    baseline_prometheus = prometheus_baseline()
    if (
        any(queues_before.values())
        or read_active_gpu_lease() is not None
        or baseline_prometheus["total"] != baseline_prometheus["up"]
        or container_exists(CONTAINER)
        or target_health(config) is not None
    ):
        raise RuntimeError("e0_remediation_smoke_preflight_not_idle")
    environment = capture_environment(config)
    b0_before = b0_cuda_inference()
    if b0_before.get("passed") is not True:
        raise RuntimeError("e0_remediation_smoke_b0_preflight")
    lease = None
    target_written = False
    started = time.monotonic()
    result: dict[str, object] = {}
    failure: str | None = None
    try:
        scale_holder(holder, 0)
        lease = acquire_scale_validation_gpu_lease(
            f"s8-v4-{attempt_id}",
            source_commit=revision,
            purpose="scale_validation_inference",
            scenario_id="E0",
            model_family="tabular",
            owner_pid=os.getpid(),
            ttl_seconds=600,
        )
        write_target(config, attempt_id)
        target_written = True
        start_triton(config, args.model_repository, args.output_root, CONTAINER)
        ready_seconds = wait_http_ready(config, CONTAINER)
        observed_gpu = container_gpu(config, CONTAINER)
        inference = infer(config, attempt_id)
        metrics_text = requests.get(
            f"http://127.0.0.1:{config.metrics_port}/metrics", timeout=10
        ).text
        metrics_path = args.output_root / "triton-metrics.txt"
        metrics_path.write_text(metrics_text, encoding="utf-8", newline="\n")
        prometheus_up_seconds = wait_target(
            config, "up", config.prometheus_timeout_seconds
        )
        prometheus_success = prometheus_query(
            "sum(nv_inference_request_success{"
            f'job="{config.prometheus_job}",attempt_id="{attempt_id}",'
            f'model="{config.model_name}",version="{config.model_version}"}})'
        )
        prometheus_inference = prometheus_query(
            "sum(nv_inference_count{"
            f'job="{config.prometheus_job}",attempt_id="{attempt_id}",'
            f'model="{config.model_name}",version="{config.model_version}"}})'
        )
        direct_success = metric_value(
            metrics_text, "nv_inference_request_success", model=config.model_name
        )
        direct_inference = metric_value(
            metrics_text, "nv_inference_count", model=config.model_name
        )
        direct_gpu_memory = metric_value(metrics_text, "nv_gpu_memory_used_bytes")
        result = {
            "ready_seconds": ready_seconds,
            "request_count": 100,
            "output": inference["output"],
            "transport_ok": True,
            "host_gpu_uuid": environment["host_gpu"]["uuid"],
            "container_gpu_uuid": observed_gpu["uuid"],
            "direct_request_success": direct_success,
            "direct_inference_count": direct_inference,
            "direct_triton_gpu_memory_used_bytes": direct_gpu_memory,
            "prometheus_request_success": prometheus_success,
            "prometheus_inference_count": prometheus_inference,
            "prometheus_up_seconds": prometheus_up_seconds,
        }
    except Exception as exc:
        failure = f"{type(exc).__name__}:{exc}"
    finally:
        stop_triton(CONTAINER)
        if target_written:
            remove_target()
            wait_target(config, None, 30)
        if lease is not None:
            release_scale_validation_gpu_lease(
                run_id=lease.run_id,
                lease_id=lease.lease_id,
                fencing_token=lease.fencing_token,
                reason="E0 remediation smoke stopped",
            )
        scale_holder(holder, holder.replicas)
    gpu_after, vram_wait = wait_vram_restore(gpu_before, config.cleanup_timeout_seconds)
    cleanup = {
        "elapsed_seconds": time.monotonic() - started,
        "vram_restore_wait_seconds": vram_wait,
        "vram_delta_mib": gpu_after["memory_used_mib"] - gpu_before["memory_used_mib"],
        "container_absent": not container_exists(CONTAINER),
        "ports_absent": ports_absent(config),
        "target_absent": target_health(config) is None,
        "lease_absent": read_active_gpu_lease() is None,
        "queues": queue_counts(),
        "temporary_kubernetes_resources_absent": temporary_kubernetes_resources_absent(),
        "prometheus_baseline": prometheus_baseline(),
        "b0_cuda_inference": b0_cuda_inference(),
        "holder_uid_match": capture_holder().uid == holder.uid,
    }
    passed = (
        failure is None
        and result.get("output") == list(config.expected_output)
        and result.get("request_count") == 100
        and result.get("direct_request_success") == 100
        and result.get("direct_inference_count") == 100
        and result.get("prometheus_request_success") == 100
        and result.get("prometheus_inference_count") == 100
        and result.get("host_gpu_uuid") == result.get("container_gpu_uuid")
        and cleanup["container_absent"] is True
        and cleanup["ports_absent"] is True
        and cleanup["target_absent"] is True
        and cleanup["lease_absent"] is True
        and cleanup["queues"] == {"active": 0, "leased": 0, "outcome_unknown": 0}
        and cleanup["temporary_kubernetes_resources_absent"] is True
        and cleanup["prometheus_baseline"] == baseline_prometheus
        and cleanup["b0_cuda_inference"]["passed"] is True
        and cleanup["holder_uid_match"] is True
    )
    payload = {
        "schema_version": "evm.s8_v4.e0_remediation_current_revision_smoke.v1",
        "attempt_id": attempt_id,
        "credit": "non_credit",
        "acceptance_credit": False,
        "diagnostic_only": True,
        "source_identity": {"branch": branch, "revision": revision, "tree_sha": tree},
        "model_identity": {
            "manifest_sha256": sha256_file(manifest_path),
            "artifact_sha256": sha256_file(artifact),
            "config_sha256": sha256_file(model_config),
        },
        "b0_preflight_passed": True,
        "result": result,
        "failure": failure,
        "cleanup": cleanup,
        "passed": passed,
        "claim_boundary": (
            "one current-revision diagnostic Triton/CUDA smoke; not an additional "
            "accepted repetition and not production or HA evidence"
        ),
    }
    write_json(args.output_root / "smoke-private.json", payload)
    print(
        canonical(
            {
                "attempt_id": attempt_id,
                "credit": "non_credit",
                "passed": passed,
                "failure": failure,
                "revision": revision,
                "output_root": str(args.output_root),
                "smoke_sha256": sha256_file(args.output_root / "smoke-private.json"),
            }
        )
    )
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
