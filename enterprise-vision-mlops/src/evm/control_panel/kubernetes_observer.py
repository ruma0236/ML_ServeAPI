from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from collections.abc import Callable, Iterable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from evm.control_panel.schemas import (
    KubernetesResourceSnapshot,
    RuntimeResource,
    RuntimeResourceList,
    State,
)


KubectlRunner = Callable[[list[str]], dict[str, Any]]
DEFAULT_NAMESPACES = ("evm-training", "evm-staging", "evm-production")


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def isoformat_z(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def parse_timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def run_kubectl_json(arguments: list[str], *, kubectl: str = "kubectl") -> dict[str, Any]:
    completed = subprocess.run(
        [kubectl, *arguments],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return json.loads(completed.stdout)


def collect_kubernetes_snapshot(
    *,
    namespaces: Iterable[str] = DEFAULT_NAMESPACES,
    cluster_context: str = "docker-desktop",
    runner: KubectlRunner = run_kubectl_json,
    now: datetime | None = None,
) -> KubernetesResourceSnapshot:
    observed = now or utc_now()
    observed_at = isoformat_z(observed)
    resources: list[RuntimeResource] = []
    try:
        node_payload = runner(["--context", cluster_context, "get", "nodes", "-o", "json"])
        resources.extend(
            resource_from_kubernetes(item, observed_at=observed_at, cluster_context=cluster_context)
            for item in node_payload.get("items", [])
        )
        for namespace in namespaces:
            payload = runner(
                [
                    "--context",
                    cluster_context,
                    "get",
                    "jobs,deployments,pods,services,persistentvolumeclaims",
                    "-n",
                    namespace,
                    "-o",
                    "json",
                ]
            )
            resources.extend(
                resource_from_kubernetes(item, observed_at=observed_at, cluster_context=cluster_context)
                for item in payload.get("items", [])
            )
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError, KeyError, TypeError) as exc:
        return KubernetesResourceSnapshot(
            schema_version="evm.w7.kubernetes_resource_snapshot.v1",
            cluster_context=cluster_context,
            observed_at=observed_at,
            collection_status="fail",
            resource_status="fail",
            message=sanitize_error(exc),
            resources=[],
        )

    status = aggregate_resource_status(resource.status for resource in resources)
    return KubernetesResourceSnapshot(
        schema_version="evm.w7.kubernetes_resource_snapshot.v1",
        cluster_context=cluster_context,
        observed_at=observed_at,
        collection_status="pass",
        resource_status=status,
        message=f"Observed {len(resources)} sanitized Kubernetes resources.",
        resources=resources,
    )


def resource_from_kubernetes(
    item: dict[str, Any], *, observed_at: str, cluster_context: str
) -> RuntimeResource:
    kind = str(item.get("kind", "Unknown"))
    metadata = item.get("metadata", {})
    status_payload = item.get("status", {})
    namespace = str(metadata.get("namespace") or "_cluster")
    name = str(metadata.get("name") or "unnamed")
    labels = metadata.get("labels", {}) or {}
    status, readiness, reason, message = state_for(kind, item)
    pod_spec = workload_pod_spec(kind, item)
    requests = container_requests(pod_spec)
    desired_replicas, ready_replicas = replica_counts(kind, item)
    gpu_capacity = None
    if kind.lower() == "node":
        gpu_capacity = string_or_none(status_payload.get("capacity", {}).get("nvidia.com/gpu"))
    owner_issue = string_or_none(labels.get("evm.openai.local/owner-issue"))
    if owner_issue is None and name.startswith(("evm-b0-", "evm-b7-")):
        owner_issue = "EVM-226"
    if owner_issue is None and kind.lower() == "node":
        owner_issue = "EVM-226"

    return RuntimeResource(
        resource_id=f"{namespace}:{kind}:{name}",
        namespace=namespace,
        kind=kind,
        name=name,
        status=status,
        node_pool=node_pool(item, cluster_context),
        readiness=readiness,
        restarts=restart_count(kind, item),
        cpu_request=string_or_none(requests.get("cpu")),
        memory_request=string_or_none(requests.get("memory")),
        gpu_request=gpu_request(requests.get("nvidia.com/gpu")),
        storage_claim=storage_claim(pod_spec, item),
        storage_root=None,
        last_transition_time=last_transition_time(item),
        owner_issue=owner_issue,
        control_actions=control_actions(kind),
        pressure=pressure_for(status),
        related_stages=related_stages(name, kind),
        observation_source="kubernetes_snapshot",
        observation_status="live",
        observed_at=observed_at,
        observation_message=message,
        reason=reason,
        desired_replicas=desired_replicas,
        ready_replicas=ready_replicas,
        gpu_capacity=gpu_capacity,
    )


def state_for(kind: str, item: dict[str, Any]) -> tuple[State, str, str | None, str | None]:
    normalized = kind.lower()
    status = item.get("status", {})
    conditions = status.get("conditions", []) or []
    if normalized == "job":
        failed = true_condition(conditions, "Failed") or true_condition(conditions, "FailureTarget")
        if failed:
            return "fail", "blocked", failed.get("reason"), failed.get("message")
        complete = true_condition(conditions, "Complete")
        if complete or int(status.get("succeeded") or 0) > 0:
            return "done", "ready", complete.get("reason") if complete else "Complete", None
        if int(status.get("active") or 0) > 0:
            return "running", "progressing", "Active", None
        return "queued", "progressing", "Pending", "Waiting for a schedulable worker."
    if normalized == "deployment":
        desired = int(item.get("spec", {}).get("replicas") or 0)
        ready = int(status.get("readyReplicas") or 0)
        if desired == 0:
            return "queued", "not_requested", "ScaledToZero", "Deployment is intentionally scaled to zero."
        if ready >= desired and int(status.get("availableReplicas") or 0) >= desired:
            return "pass", "ready", "Available", None
        failed = false_condition(conditions, "Progressing")
        if failed:
            return "fail", "blocked", failed.get("reason"), failed.get("message")
        return "running", "progressing", "Progressing", None
    if normalized == "pod":
        phase = str(status.get("phase") or "Unknown")
        scheduled_failure = false_condition(conditions, "PodScheduled")
        if phase == "Failed":
            return "fail", "blocked", status.get("reason"), status.get("message")
        if scheduled_failure:
            return "blocked", "blocked", scheduled_failure.get("reason"), scheduled_failure.get("message")
        if phase == "Succeeded":
            return "done", "ready", "Succeeded", None
        if phase == "Running":
            ready = true_condition(conditions, "Ready")
            return ("pass", "ready", "Ready", None) if ready else ("running", "progressing", "Running", None)
        return "queued", "progressing", phase, status.get("message")
    if normalized == "persistentvolumeclaim":
        phase = str(status.get("phase") or "Pending")
        if phase == "Bound":
            return "pass", "ready", "Bound", None
        if phase == "Lost":
            return "fail", "blocked", "Lost", None
        return "queued", "progressing", phase, None
    if normalized == "node":
        ready = true_condition(conditions, "Ready")
        gpu = status.get("capacity", {}).get("nvidia.com/gpu")
        if not ready:
            failed = false_condition(conditions, "Ready")
            return "fail", "blocked", failed.get("reason") if failed else "NotReady", failed.get("message") if failed else None
        if not gpu:
            return "warn", "ready", "GpuNotAdvertised", "Node is Ready but nvidia.com/gpu is not advertised."
        return "pass", "ready", "Ready", None
    return "pass", "ready", "Observed", None


def workload_pod_spec(kind: str, item: dict[str, Any]) -> dict[str, Any]:
    normalized = kind.lower()
    if normalized in {"job", "deployment"}:
        return item.get("spec", {}).get("template", {}).get("spec", {}) or {}
    if normalized == "pod":
        return item.get("spec", {}) or {}
    return {}


def container_requests(pod_spec: dict[str, Any]) -> dict[str, str]:
    requests: dict[str, str] = {}
    for container in pod_spec.get("containers", []) or []:
        for key, value in (container.get("resources", {}).get("requests", {}) or {}).items():
            requests.setdefault(str(key), str(value))
    return requests


def replica_counts(kind: str, item: dict[str, Any]) -> tuple[int | None, int | None]:
    if kind.lower() != "deployment":
        return None, None
    return (
        int(item.get("spec", {}).get("replicas") or 0),
        int(item.get("status", {}).get("readyReplicas") or 0),
    )


def restart_count(kind: str, item: dict[str, Any]) -> int:
    if kind.lower() != "pod":
        return 0
    return sum(int(status.get("restartCount") or 0) for status in item.get("status", {}).get("containerStatuses", []) or [])


def storage_claim(pod_spec: dict[str, Any], item: dict[str, Any]) -> str | None:
    if str(item.get("kind", "")).lower() == "persistentvolumeclaim":
        return string_or_none(item.get("metadata", {}).get("name"))
    for volume in pod_spec.get("volumes", []) or []:
        claim = volume.get("persistentVolumeClaim", {}).get("claimName")
        if claim:
            return str(claim)
    return None


def node_pool(item: dict[str, Any], cluster_context: str) -> str:
    kind = str(item.get("kind", "")).lower()
    if kind == "node":
        return str(item.get("metadata", {}).get("name") or cluster_context)
    return str(item.get("spec", {}).get("nodeName") or cluster_context)


def last_transition_time(item: dict[str, Any]) -> str | None:
    conditions = item.get("status", {}).get("conditions", []) or []
    timestamps = [condition.get("lastTransitionTime") for condition in conditions if condition.get("lastTransitionTime")]
    if timestamps:
        return str(sorted(timestamps)[-1])
    return string_or_none(item.get("metadata", {}).get("creationTimestamp"))


def true_condition(conditions: list[dict[str, Any]], condition_type: str) -> dict[str, Any] | None:
    return next(
        (condition for condition in conditions if condition.get("type") == condition_type and condition.get("status") == "True"),
        None,
    )


def false_condition(conditions: list[dict[str, Any]], condition_type: str) -> dict[str, Any] | None:
    return next(
        (condition for condition in conditions if condition.get("type") == condition_type and condition.get("status") == "False"),
        None,
    )


def gpu_request(value: Any) -> str | None:
    return f"{value} x GPU" if value not in {None, "", "0", 0} else None


def string_or_none(value: Any) -> str | None:
    return None if value is None or value == "" else str(value)


def control_actions(kind: str) -> list[str]:
    normalized = kind.lower()
    if normalized == "deployment":
        return ["view", "restart_dry_run", "scale_dry_run"]
    if normalized == "job":
        return ["view", "rerun_dry_run", "cancel_dry_run"]
    return ["view"]


def pressure_for(status: State) -> State:
    if status in {"fail", "blocked"}:
        return "fail"
    if status == "warn":
        return "warn"
    if status in {"running", "queued"}:
        return status
    return "pass"


def related_stages(name: str, kind: str) -> list[str]:
    if name == "evm-b0-expedited-training":
        return ["EfficientNet B0 Kubernetes Training"]
    if name == "evm-b0-production":
        return ["EfficientNet B0 Kubernetes Serving"]
    if name == "evm-b7-training":
        return ["EfficientNet B7 Kubernetes Training"]
    if name == "evm-b7-serving":
        return ["EfficientNet B7 Kubernetes Serving"]
    if kind.lower() == "node":
        return ["Kubernetes Capacity"]
    return []


def aggregate_resource_status(statuses: Iterable[State]) -> State:
    order: dict[State, int] = {
        "fail": 8,
        "blocked": 7,
        "cancelled": 6,
        "warn": 5,
        "running": 4,
        "queued": 3,
        "unknown": 2,
        "pass": 1,
        "done": 0,
    }
    values = list(statuses)
    return max(values, key=lambda value: order[value]) if values else "unknown"


def sanitize_error(exc: BaseException) -> str:
    message = " ".join(str(exc).split())
    return message[:500] or exc.__class__.__name__


def write_snapshot(
    snapshot: KubernetesResourceSnapshot,
    output_path: Path,
    *,
    history_root: Path | None = None,
    max_history: int = 500,
) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    payload = snapshot.model_dump(mode="json")
    rendered = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    temporary = output_path.with_suffix(f"{output_path.suffix}.{os.getpid()}.tmp")
    temporary.write_text(rendered, encoding="utf-8")
    replace_with_retry(temporary, output_path)
    if history_root is None:
        return
    digest = snapshot_state_digest(payload)
    digest_path = history_root.parent / ".latest_digest"
    previous_digest = digest_path.read_text(encoding="ascii").strip() if digest_path.exists() else ""
    if digest == previous_digest:
        return
    try:
        history_root.mkdir(parents=True, exist_ok=True)
        stamp = snapshot.observed_at.replace(":", "").replace("-", "")
        history_path = history_root / f"{stamp}-{digest[:12]}.json"
        history_path.write_text(rendered, encoding="utf-8")
        digest_path.write_text(digest + "\n", encoding="ascii")
        histories = sorted(history_root.glob("*.json"), key=lambda path: path.stat().st_mtime)
        for stale_path in histories[:-max_history]:
            stale_path.unlink()
    except OSError as exc:
        print(f"Kubernetes observer history write failed: {sanitize_error(exc)}", file=sys.stderr)


def snapshot_state_digest(payload: dict[str, Any]) -> str:
    canonical = dict(payload)
    canonical.pop("observed_at", None)
    canonical["resources"] = [
        {key: value for key, value in resource.items() if key != "observed_at"}
        for resource in payload.get("resources", [])
    ]
    return hashlib.sha256(json.dumps(canonical, sort_keys=True).encode("utf-8")).hexdigest()


def replace_with_retry(source: Path, target: Path, *, attempts: int = 20) -> None:
    for attempt in range(attempts):
        try:
            os.replace(source, target)
            return
        except PermissionError:
            if attempt + 1 == attempts:
                raise
            time.sleep(min(0.05 * (attempt + 1), 0.5))


def load_kubernetes_resource_snapshot(
    path: Path | None = None,
    *,
    now: datetime | None = None,
    stale_after_seconds: float | None = None,
) -> RuntimeResourceList:
    source_path = path or Path(
        os.getenv(
            "EVM_KUBERNETES_RESOURCE_SNAPSHOT_PATH",
            "/app/artifacts/w7/kubernetes_observer/latest.json",
        )
    )
    stale_limit = stale_after_seconds
    if stale_limit is None:
        try:
            stale_limit = max(1.0, float(os.getenv("EVM_KUBERNETES_SNAPSHOT_STALE_SECONDS", "15")))
        except ValueError:
            stale_limit = 15.0
    if not source_path.exists():
        return RuntimeResourceList(
            resources=[],
            observation_status="unavailable",
            snapshot_uri=str(source_path),
            observation_message="Kubernetes resource snapshot is not available.",
        )
    try:
        snapshot = KubernetesResourceSnapshot.model_validate_json(source_path.read_text(encoding="utf-8"))
        age_seconds = max(0.0, ((now or utc_now()) - parse_timestamp(snapshot.observed_at)).total_seconds())
    except (OSError, ValueError) as exc:
        return RuntimeResourceList(
            resources=[],
            observation_status="unavailable",
            snapshot_uri=str(source_path),
            observation_message=f"Kubernetes snapshot validation failed: {sanitize_error(exc)}",
        )
    if snapshot.collection_status == "fail":
        observation_status = "unavailable"
    else:
        observation_status = "live" if age_seconds <= stale_limit else "stale"
    resources = [
        resource.model_copy(update={"observation_status": observation_status})
        for resource in snapshot.resources
    ]
    return RuntimeResourceList(
        resources=resources,
        observation_status=observation_status,
        observed_at=snapshot.observed_at,
        snapshot_age_seconds=round(age_seconds, 3),
        cluster_context=snapshot.cluster_context,
        snapshot_uri=str(source_path),
        observation_message=snapshot.message,
    )


def merge_runtime_resources(
    projected: list[RuntimeResource], observed: RuntimeResourceList
) -> RuntimeResourceList:
    resources = {
        resource.resource_id: resource.model_copy(
            update={"observation_source": "cycle_projection", "observation_status": "projected"}
        )
        for resource in projected
    }
    for resource in observed.resources:
        resources[resource.resource_id] = resource
    return observed.model_copy(update={"resources": [resources[key] for key in sorted(resources)]})


def observer_loop(
    *,
    output_path: Path,
    history_root: Path,
    namespaces: tuple[str, ...],
    cluster_context: str,
    interval_seconds: float,
    max_history: int,
) -> None:
    while True:
        snapshot = collect_kubernetes_snapshot(
            namespaces=namespaces,
            cluster_context=cluster_context,
        )
        try:
            write_snapshot(snapshot, output_path, history_root=history_root, max_history=max_history)
        except OSError as exc:
            print(f"Kubernetes observer snapshot write failed: {sanitize_error(exc)}", file=sys.stderr)
        if interval_seconds <= 0:
            return
        time.sleep(interval_seconds)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Write sanitized Kubernetes resource snapshots.")
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--history-root", type=Path)
    parser.add_argument("--namespaces", default=",".join(DEFAULT_NAMESPACES))
    parser.add_argument("--cluster-context", default="docker-desktop")
    parser.add_argument("--interval-seconds", type=float, default=0.0)
    parser.add_argument("--max-history", type=int, default=500)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    namespaces = tuple(value.strip() for value in args.namespaces.split(",") if value.strip())
    history_root = args.history_root or args.output.parent / "history"
    observer_loop(
        output_path=args.output,
        history_root=history_root,
        namespaces=namespaces,
        cluster_context=args.cluster_context,
        interval_seconds=max(0.0, args.interval_seconds),
        max_history=max(1, args.max_history),
    )


if __name__ == "__main__":
    main()
