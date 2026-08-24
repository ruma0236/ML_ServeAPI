from __future__ import annotations

import argparse
import json
import subprocess
import sys
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from evm.scale_validation.evidence import write_public_json  # noqa: E402
from evm.scale_validation.s1_runtime import canonical_write, sha256_file  # noqa: E402
from evm.scale_validation.s8_closure import (  # noqa: E402
    SMOKE_PATH,
    SMOKE_SCHEMA_VERSION,
    SMOKE_SOURCE_PATHS,
)
from evm.scale_validation.s8_runtime import (  # noqa: E402
    current_runtime_cleanup,
    git_blob_identity,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Capture the S8 current-revision smoke.")
    parser.add_argument("--private-closure-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=ROOT / SMOKE_PATH)
    parser.add_argument("--serving-url", default="http://127.0.0.1:30800")
    parser.add_argument("--prometheus-url", default="http://127.0.0.1:9090")
    parser.add_argument(
        "--sample-image-uri",
        default="/mnt/evm-data/data/raw/industrial/visa/candle/Data/Images/Anomaly/000.JPG",
    )
    return parser.parse_args()


def run_json(command: list[str]) -> dict[str, Any]:
    completed = subprocess.run(
        command,
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )
    payload = json.loads(completed.stdout)
    if not isinstance(payload, dict):
        raise RuntimeError(f"mapping expected from {' '.join(command[:2])}")
    return payload


def request_json(url: str, *, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"} if body else {},
        method="POST" if body else "GET",
    )
    with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310
        value = json.loads(response.read())
    if not isinstance(value, dict):
        raise RuntimeError(f"mapping expected from {url}")
    return value


def docker_health(name: str) -> str:
    return subprocess.run(
        [
            "docker",
            "inspect",
            "--format",
            "{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}",
            name,
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
        timeout=15,
    ).stdout.strip()


def queue_counts() -> dict[str, int]:
    sql = (
        "SELECT count(*) FILTER (WHERE state IN "
        "('available','retry_wait','leased','runtime_pending','outcome_unknown'))," 
        "count(*) FILTER (WHERE state='leased'),"
        "count(*) FILTER (WHERE state='outcome_unknown') "
        "FROM evm_control_plane.task_admission_queue;"
    )
    shell = (
        'psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" '
        f'-At -F "|" -c "{sql}"'
    )
    completed = subprocess.run(
        ["docker", "exec", "evm-control-plane-postgres", "sh", "-lc", shell],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
        timeout=20,
    )
    values = [int(value) for value in completed.stdout.strip().split("|")]
    if len(values) != 3:
        raise RuntimeError("unexpected queue count projection")
    return {"active": values[0], "leased": values[1], "outcome_unknown": values[2]}


def deployment(namespace: str, name: str) -> dict[str, Any]:
    return run_json(["kubectl", "get", "deployment", name, "-n", namespace, "-o", "json"])


def main() -> int:
    args = parse_args()
    revision = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    if subprocess.run(["git", "diff", "--quiet"], cwd=ROOT, check=False).returncode:
        raise RuntimeError("s8_smoke_requires_clean_tracked_worktree")
    branch = subprocess.run(
        ["git", "branch", "--show-current"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    generated_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")

    source = deployment("evm-production", "evm-b0-production")
    target_b0 = deployment("evm-staging", "evm-b0-staging")
    target_b7 = deployment("evm-staging", "evm-b7-serving")
    pods = run_json(
        [
            "kubectl",
            "get",
            "pods",
            "-n",
            "evm-production",
            "-l",
            "app.kubernetes.io/name=evm-b0-production",
            "-o",
            "json",
        ]
    )
    ready = request_json(f"{args.serving_url}/ready")
    inference = request_json(
        f"{args.serving_url}/predict",
        payload={"image_uri": args.sample_image_uri},
    )
    prometheus = request_json(f"{args.prometheus_url}/api/v1/targets")
    targets = list(dict(prometheus.get("data", {})).get("activeTargets", []))
    target_summary = [
        {
            "job": dict(item.get("labels", {})).get("job"),
            "health": item.get("health"),
            "last_error_empty": not bool(item.get("lastError")),
        }
        for item in targets
    ]
    queues = queue_counts()
    cleanup = current_runtime_cleanup(ROOT)
    source_status = dict(source.get("status", {}))
    source_spec = dict(source.get("spec", {}))
    pod_items = list(pods.get("items", []))
    historical_terminal = sum(
        str(dict(item.get("status", {})).get("phase")) in {"Failed", "Succeeded"}
        for item in pod_items
    )
    checks = {
        "api_healthy": docker_health("evm-api") == "healthy",
        "queue_worker_healthy": docker_health("evm-task-queue-worker") == "healthy",
        "control_plane_postgresql_healthy": docker_health("evm-control-plane-postgres")
        == "healthy",
        "source_serving_ready": int(source_status.get("readyReplicas", 0)) == 1
        and int(source_status.get("availableReplicas", 0)) == 1
        and int(source_spec.get("replicas", 0)) == 1,
        "target_serving_scaled_zero": int(dict(target_b0.get("spec", {})).get("replicas", -1))
        == 0
        and int(dict(target_b7.get("spec", {})).get("replicas", -1)) == 0,
        "real_cuda_inference": ready.get("device") == "cuda"
        and ready.get("cuda_available") is True
        and inference.get("device") == "cuda"
        and bool(inference.get("prediction")),
        "prometheus_all_targets_up": bool(targets)
        and all(item["health"] == "up" and item["last_error_empty"] for item in target_summary),
        "prometheus_targets_total": len(targets),
        "prometheus_targets_up": sum(item["health"] == "up" for item in target_summary),
        "queue_active_zero": queues["active"] == 0,
        "queue_leased_zero": queues["leased"] == 0,
        "queue_outcome_unknown_zero": queues["outcome_unknown"] == 0,
        "s8_processes_removed": not cleanup["marker_processes"],
        "s8_containers_removed": not cleanup["temporary_containers"],
        "worker_metrics_port_available": bool(cleanup["worker_metrics_port_available"]),
        "historical_terminal_serving_pods": historical_terminal,
    }
    raw = {
        "schema_version": "evm.s8_current_revision_runtime_smoke_private.v1",
        "generated_at": generated_at,
        "source_revision": revision,
        "checks": checks,
        "runtime_identity": {
            "source_deployment_uid": dict(source.get("metadata", {})).get("uid"),
            "source_image": dict(source_spec.get("template", {}))
            .get("spec", {})
            .get("containers", [{}])[0]
            .get("image"),
            "ready_payload": ready,
            "inference_payload": inference,
            "queue_counts": queues,
            "prometheus_targets": target_summary,
            "cleanup": cleanup,
        },
    }
    private_path = args.private_closure_root / "runtime" / "s8-current-revision-smoke-private.json"
    canonical_write(private_path, raw)
    public = {
        "schema_version": SMOKE_SCHEMA_VERSION,
        "generated_at": generated_at,
        "status": "verified" if all(
            value is True
            for key, value in checks.items()
            if key not in {"prometheus_targets_total", "prometheus_targets_up", "historical_terminal_serving_pods"}
        ) else "failed",
        "verdict": "passed" if all(
            value is True
            for key, value in checks.items()
            if key not in {"prometheus_targets_total", "prometheus_targets_up", "historical_terminal_serving_pods"}
        ) else "failed",
        "acceptance_credit": False,
        "source_identity": {
            "revision": revision,
            "branch": branch,
            "git_blobs": {
                label: git_blob_identity(ROOT, revision, path)
                for label, path in SMOKE_SOURCE_PATHS.items()
            },
        },
        "checks": checks,
        "private_evidence": {
            "path": private_path.relative_to(args.private_closure_root).as_posix(),
            "bytes": private_path.stat().st_size,
            "sha256": sha256_file(private_path),
        },
        "claim_boundary": (
            "Current-revision diagnostic on one local physical node and one CUDA device; "
            "no customer traffic, production SLA, HA/DR, or multi-GPU claim. Historical "
            "terminal Pods are disclosed but excluded from the active serving baseline."
        ),
    }
    write_public_json(args.output, public)
    print(json.dumps({"status": public["status"], "checks": checks}, sort_keys=True))
    return 0 if public["status"] == "verified" else 1


if __name__ == "__main__":
    raise SystemExit(main())
