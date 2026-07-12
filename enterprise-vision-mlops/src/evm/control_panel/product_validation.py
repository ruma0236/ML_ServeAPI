from __future__ import annotations

import argparse
import json
import math
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable

import requests


KubectlRunner = Callable[[list[str]], dict[str, Any]]


def utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def request_json(
    session: requests.Session,
    method: str,
    url: str,
    *,
    payload: dict[str, Any] | None = None,
    params: dict[str, str] | None = None,
    timeout: float = 30.0,
) -> dict[str, Any]:
    response = session.request(
        method,
        url,
        json=payload,
        params=params,
        timeout=timeout,
    )
    response.raise_for_status()
    rendered = response.json()
    if not isinstance(rendered, dict):
        raise ValueError(f"expected JSON object from {url}")
    return rendered


def run_kubectl_json(arguments: list[str]) -> dict[str, Any]:
    completed = subprocess.run(
        ["kubectl", *arguments],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    payload = json.loads(completed.stdout)
    if not isinstance(payload, dict):
        raise ValueError("kubectl did not return a JSON object")
    return payload


def prometheus_query(
    session: requests.Session,
    prometheus_url: str,
    query: str,
) -> list[dict[str, Any]]:
    payload = request_json(
        session,
        "GET",
        f"{prometheus_url.rstrip('/')}/api/v1/query",
        params={"query": query},
    )
    result = payload.get("data", {}).get("result", [])
    return result if isinstance(result, list) else []


def result_value(result: list[dict[str, Any]]) -> float | None:
    if not result:
        return None
    value = result[0].get("value")
    if not isinstance(value, list) or len(value) < 2:
        return None
    try:
        return float(value[1])
    except (TypeError, ValueError):
        return None


def summed_result(result: list[dict[str, Any]]) -> float:
    total = 0.0
    for item in result:
        value = item.get("value")
        if not isinstance(value, list) or len(value) < 2:
            continue
        try:
            total += float(value[1])
        except (TypeError, ValueError):
            continue
    return total


def task_by_id(tasks: list[dict[str, Any]], task_id: str) -> dict[str, Any]:
    return next((item for item in tasks if item.get("task_id") == task_id), {})


def product_evidence_state(
    payload: dict[str, Any],
    *,
    deployment_intent_id: str,
    model_digest: str,
) -> tuple[bool, float | None, int]:
    deployment = payload.get("deployment", {})
    inference = payload.get("product_inference", {})
    ready = inference.get("ready", {}) if isinstance(inference, dict) else {}
    monitoring = payload.get("monitoring", {})
    target = monitoring.get("prometheus_target", {}) if isinstance(monitoring, dict) else {}
    p95_latency_ms = monitoring.get("p95_latency_ms") if isinstance(monitoring, dict) else None
    try:
        p95_latency_ms = float(p95_latency_ms)
    except (TypeError, ValueError):
        p95_latency_ms = None
    try:
        inference_request_total = float(monitoring.get("inference_request_total", 0) or 0)
    except (TypeError, ValueError):
        inference_request_total = 0.0
    valid = bool(
        payload.get("status") == "pass"
        and isinstance(deployment, dict)
        and deployment.get("intent_id") == deployment_intent_id
        and deployment.get("state") == "applied"
        and isinstance(ready, dict)
        and ready.get("status") == "ok"
        and ready.get("model_loaded") is True
        and ready.get("model_sha256") == model_digest
        and isinstance(target, dict)
        and target.get("health") == "up"
        and p95_latency_ms is not None
        and math.isfinite(p95_latency_ms)
        and p95_latency_ms > 0
        and inference_request_total >= 2
    )
    healthy_targets = (
        1 if isinstance(target, dict) and target.get("health") == "up" else 0
    )
    return valid, p95_latency_ms, healthy_targets


def collect_product_validation(
    args: argparse.Namespace,
    *,
    session: requests.Session | None = None,
    kubectl_runner: KubectlRunner = run_kubectl_json,
) -> dict[str, Any]:
    http = session or requests.Session()
    control_panel_url = args.control_panel_url.rstrip("/")
    serving_url = args.serving_url.rstrip("/")
    prometheus_url = args.prometheus_url.rstrip("/")
    mlflow_url = args.mlflow_url.rstrip("/")

    cycle = request_json(http, "GET", f"{control_panel_url}/control-panel/v1/cycles/latest")
    task_payload = request_json(
        http,
        "GET",
        f"{control_panel_url}/control-panel/v1/tasks",
        params={"refresh_runtime": "true"},
        timeout=60,
    )
    tasks = task_payload.get("tasks", [])
    tasks = tasks if isinstance(tasks, list) else []
    data_task = task_by_id(tasks, args.data_task_id)
    model_task = task_by_id(tasks, args.model_task_id)

    deployment_intent = cycle.get("latest_deployment_intent", {})
    mlflow_ref = cycle.get("mlflow", {})
    run_id = str(mlflow_ref.get("run_id") or "")
    mlflow_run = request_json(
        http,
        "GET",
        f"{mlflow_url}/api/2.0/mlflow/runs/get",
        params={"run_id": run_id},
    ).get("run", {})

    health = request_json(http, "GET", f"{serving_url}/health")
    ready = request_json(http, "GET", f"{serving_url}/ready")
    normal = request_json(
        http,
        "POST",
        f"{serving_url}/predict",
        payload={"image_uri": args.normal_image_uri},
        timeout=60,
    )
    anomaly = request_json(
        http,
        "POST",
        f"{serving_url}/predict",
        payload={"image_uri": args.anomaly_image_uri},
        timeout=60,
    )

    if args.scrape_wait_seconds > 0:
        time.sleep(args.scrape_wait_seconds)
    targets_payload = request_json(http, "GET", f"{prometheus_url}/api/v1/targets")
    active_targets = targets_payload.get("data", {}).get("activeTargets", [])
    active_targets = active_targets if isinstance(active_targets, list) else []
    prometheus_target = next(
        (
            item
            for item in active_targets
            if isinstance(item, dict)
            and isinstance(item.get("labels"), dict)
            and item["labels"].get("job") == args.prometheus_job
        ),
        {},
    )
    loaded_result = prometheus_query(
        http,
        prometheus_url,
        f'evm_serving_model_loaded{{job="{args.prometheus_job}"}}',
    )
    request_result = prometheus_query(
        http,
        prometheus_url,
        f'evm_serving_inference_requests_total{{job="{args.prometheus_job}"}}',
    )
    p95_result = prometheus_query(
        http,
        prometheus_url,
        "histogram_quantile(0.95, sum by (le) "
        f'(evm_serving_inference_latency_seconds_bucket{{job="{args.prometheus_job}"}}))',
    )
    p95_seconds = result_value(p95_result)
    p95_latency_ms = p95_seconds * 1000 if p95_seconds is not None else None
    inference_request_total = summed_result(request_result)

    deployment = kubectl_runner(
        ["-n", args.namespace, "get", "deployment", args.deployment, "-o", "json"]
    )
    selector_labels = deployment.get("spec", {}).get("selector", {}).get("matchLabels", {})
    selector = ",".join(f"{key}={value}" for key, value in sorted(selector_labels.items()))
    pods = kubectl_runner(
        ["-n", args.namespace, "get", "pods", "-l", selector, "-o", "json"]
    )
    pod = next(iter(pods.get("items", [])), {})
    container_status = next(iter(pod.get("status", {}).get("containerStatuses", [])), {})
    deployment_annotation = (
        deployment.get("spec", {})
        .get("template", {})
        .get("metadata", {})
        .get("annotations", {})
        .get("evm.openai.local/deployment-intent", "")
    )

    release_ref = str(cycle.get("environment", {}).get("release_ref") or "")
    intent_ci_commit = str(deployment_intent.get("ci_evidence", {}).get("commit_sha") or "")
    model_digest = str(deployment_intent.get("model_digest") or "")
    matrix_policy = cycle.get("model_matrix", {}).get("real_test_policy", {})
    checks = {
        "cycle_pass": cycle.get("status") == "pass",
        "production_environment": (
            cycle.get("environment", {}).get("tier") == "production"
            and cycle.get("environment", {}).get("namespace") == args.namespace
        ),
        "ci_attested": (
            cycle.get("ci_evidence", {}).get("status") == "pass"
            and intent_ci_commit == release_ref
            and len(release_ref) == 40
        ),
        "readiness_pass": cycle.get("readiness_evaluation", {}).get("status") == "pass",
        "drift_pass": cycle.get("drift", {}).get("status") == "pass",
        "cdct_pass": cycle.get("cdct_gate", {}).get("status") == "pass",
        "no_mock_no_smoke": (
            matrix_policy.get("requires_real_dataset") is True
            and matrix_policy.get("requires_real_training") is True
            and matrix_policy.get("mock_allowed") is False
            and matrix_policy.get("smoke_allowed") is False
        ),
        "data_task_done": (
            data_task.get("status") == "done" and data_task.get("runtime_state") == "success"
        ),
        "model_task_done": (
            model_task.get("status") == "done" and model_task.get("runtime_state") == "complete"
        ),
        "mlflow_finished": mlflow_run.get("info", {}).get("status") == "FINISHED",
        "deployment_applied": (
            deployment_intent.get("state") == "applied"
            and deployment_intent.get("target_environment") == "production"
            and deployment_intent.get("target_namespace") == args.namespace
        ),
        "immutable_image": "@sha256:" in str(deployment_intent.get("image_digest") or ""),
        "kubernetes_ready": (
            deployment.get("status", {}).get("readyReplicas")
            == deployment.get("spec", {}).get("replicas")
            and container_status.get("ready") is True
            and pod.get("status", {}).get("phase") == "Running"
        ),
        "deployment_annotation": str(deployment_intent.get("intent_id") or "")
        in deployment_annotation,
        "model_loaded": ready.get("status") == "ok" and ready.get("model_loaded") is True,
        "model_digest_match": ready.get("model_sha256") == model_digest,
        "cuda_inference": ready.get("cuda_available") is True and ready.get("device") == "cuda",
        "normal_prediction": normal.get("prediction") == args.normal_label,
        "anomaly_prediction": anomaly.get("prediction") == args.anomaly_label,
        "prometheus_target_up": prometheus_target.get("health") == "up",
        "prometheus_model_loaded": result_value(loaded_result) == 1.0,
        "production_latency_observed": p95_latency_ms is not None and p95_latency_ms > 0,
        "inference_requests_observed": inference_request_total >= 2,
    }
    blockers = [name for name, passed in checks.items() if not passed]
    evidence = {
        "schema_version": "evm.w7.final_product_validation.v1",
        "status": "pass" if not blockers else "blocked",
        "blockers": blockers,
        "checked_at": utc_now(),
        "scope": "local-docker-desktop-production-validation",
        "external_production_claim_allowed": False,
        "checks": checks,
        "git": {
            "commit": release_ref,
            "ci_workflow_run_id": cycle.get("ci_evidence", {}).get("workflow_run_id"),
            "ci_status": cycle.get("ci_evidence", {}).get("status"),
            "ci_bundle_digest": deployment_intent.get("ci_bundle_digest"),
        },
        "control_panel": {
            "url": control_panel_url,
            "cycle_id": cycle.get("cycle_id"),
            "status": cycle.get("status"),
            "environment": cycle.get("environment"),
            "readiness": cycle.get("readiness_evaluation", {}).get("status"),
            "drift": cycle.get("drift", {}).get("status"),
            "cdct": cycle.get("cdct_gate", {}).get("status"),
            "cdct_approved_by": cycle.get("cdct_gate", {}).get("approved_by"),
        },
        "data_pipeline": data_task,
        "model_pipeline": {
            "task": model_task,
            "mlflow_run_id": run_id,
            "mlflow_status": mlflow_run.get("info", {}).get("status"),
            "experiment_id": mlflow_run.get("info", {}).get("experiment_id"),
            "artifact_uri": mlflow_run.get("info", {}).get("artifact_uri"),
        },
        "deployment": {
            "intent_id": deployment_intent.get("intent_id"),
            "state": deployment_intent.get("state"),
            "model_digest": model_digest,
            "image_digest": deployment_intent.get("image_digest"),
            "target_namespace": deployment_intent.get("target_namespace"),
            "approver": deployment_intent.get("approver"),
            "deployment_annotation": deployment_annotation,
            "available_replicas": deployment.get("status", {}).get("availableReplicas", 0),
            "ready_replicas": deployment.get("status", {}).get("readyReplicas", 0),
            "pod_phase": pod.get("status", {}).get("phase"),
            "pod_ready": container_status.get("ready", False),
            "pod_restarts": container_status.get("restartCount", 0),
        },
        "product_inference": {
            "nodeport_endpoint": serving_url,
            "health": health,
            "ready": ready,
            "normal": normal,
            "anomaly": anomaly,
        },
        "monitoring": {
            "prometheus_target": {
                "health": prometheus_target.get("health"),
                "scrape_url": prometheus_target.get("scrapeUrl"),
                "last_error": prometheus_target.get("lastError"),
                "last_scrape": prometheus_target.get("lastScrape"),
            },
            "model_loaded": loaded_result,
            "inference_requests": request_result,
            "inference_request_total": inference_request_total,
            "p95_latency_ms": p95_latency_ms,
        },
    }
    return evidence


def write_evidence(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Validate an applied model product end to end.")
    parser.add_argument("--control-panel-url", default="http://127.0.0.1:4173")
    parser.add_argument("--serving-url", default="http://127.0.0.1:30800")
    parser.add_argument("--prometheus-url", default="http://127.0.0.1:9090")
    parser.add_argument("--mlflow-url", default="http://127.0.0.1:5000")
    parser.add_argument("--prometheus-job", default="evm-b0-production")
    parser.add_argument("--namespace", default="evm-production")
    parser.add_argument("--deployment", default="evm-b0-production")
    parser.add_argument("--data-task-id", required=True)
    parser.add_argument("--model-task-id", required=True)
    parser.add_argument("--normal-image-uri", required=True)
    parser.add_argument("--anomaly-image-uri", required=True)
    parser.add_argument("--normal-label", default="normal")
    parser.add_argument("--anomaly-label", default="anomaly")
    parser.add_argument("--scrape-wait-seconds", type=float, default=15.0)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--require-pass", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        evidence = collect_product_validation(args)
    except (OSError, ValueError, requests.RequestException, subprocess.SubprocessError) as exc:
        evidence = {
            "schema_version": "evm.w7.final_product_validation.v1",
            "status": "blocked",
            "blockers": [f"validation_runtime_error:{type(exc).__name__}"],
            "checked_at": utc_now(),
            "scope": "local-docker-desktop-production-validation",
            "external_production_claim_allowed": False,
        }
    write_evidence(args.output, evidence)
    print(json.dumps(evidence, indent=2, sort_keys=True))
    return 2 if args.require_pass and evidence.get("status") != "pass" else 0


if __name__ == "__main__":
    raise SystemExit(main())
