from __future__ import annotations

import json
import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable

from evm.control_panel.lifecycle_kubernetes import ServingBundle, TrainingBundle
from evm.control_panel.lifecycle_runs import LifecycleRun


Runner = Callable[..., subprocess.CompletedProcess[str]]
HANDOFF_SCHEMA = "evm.lifecycle_gpu_handoff.v1"


class GpuHandoffError(RuntimeError):
    pass


def handoff_enabled() -> bool:
    return os.getenv("EVM_LIFECYCLE_SINGLE_GPU_HANDOFF_ENABLED", "false").lower() in {
        "1",
        "true",
        "yes",
    }


def handoff_path(run: LifecycleRun) -> Path:
    return Path(run.artifact_root) / "kubernetes" / "gpu_handoff.json"


def training_handoff_path(run: LifecycleRun) -> Path:
    return Path(run.artifact_root) / "kubernetes" / "training_gpu_handoff.json"


def acquire_gpu_handoff(
    run: LifecycleRun,
    serving: ServingBundle,
    *,
    runner: Runner,
) -> Path | None:
    if not handoff_enabled():
        return None
    path = handoff_path(run)
    existing = read_payload(path)
    if existing.get("state") == "acquired":
        return path
    if existing.get("state") in {"acquiring", "releasing", "release_failed"}:
        release_gpu_handoff(run, serving, runner=runner, reason="stale_lease_recovery")

    return acquire_holders(
        run,
        path,
        target={
            "kind": "Deployment",
            "namespace": serving.namespace,
            "name": serving.deployment_name,
        },
        excluded_holder=(serving.namespace, serving.deployment_name),
        runner=runner,
    )


def acquire_training_gpu_handoff(
    run: LifecycleRun,
    training: TrainingBundle,
    *,
    runner: Runner,
) -> Path | None:
    if not handoff_enabled():
        return None
    path = training_handoff_path(run)
    existing = read_payload(path)
    if existing.get("state") == "acquired":
        return path
    if existing.get("state") in {"acquiring", "releasing", "release_failed"}:
        release_training_gpu_handoff(
            run,
            training,
            runner=runner,
            reason="stale_lease_recovery",
        )
    return acquire_holders(
        run,
        path,
        target={
            "kind": "Job",
            "namespace": training.namespace,
            "name": training.job_name,
        },
        excluded_holder=None,
        runner=runner,
    )


def release_gpu_handoff(
    run: LifecycleRun,
    serving: ServingBundle,
    *,
    runner: Runner,
    reason: str,
) -> Path | None:
    path = handoff_path(run)
    if not path.is_file():
        return None
    lease = read_payload(path)
    if lease.get("state") in {"released", "not_required"}:
        return path
    holders = lease.get("holders") if isinstance(lease.get("holders"), list) else []
    lease["state"] = "releasing"
    lease["release_reason"] = reason
    lease.setdefault("commands", [])
    lease.setdefault("blockers", [])
    write_payload(path, lease)

    errors: list[str] = []
    try:
        scale_target_to_zero(runner, serving, lease)
    except GpuHandoffError as exc:
        errors.append(str(exc))
    try:
        restore_holders(runner, holders, lease, tolerate_failure=False)
    except GpuHandoffError as exc:
        errors.append(str(exc))

    lease["released_at"] = utc_now()
    if errors:
        lease["state"] = "release_failed"
        lease["blockers"] = sorted(set([*lease["blockers"], *errors]))
        write_payload(path, lease)
        raise GpuHandoffError(";".join(errors))
    lease["state"] = "released"
    write_payload(path, lease)
    return path


def release_training_gpu_handoff(
    run: LifecycleRun,
    training: TrainingBundle,
    *,
    runner: Runner,
    reason: str,
) -> Path | None:
    del training
    path = training_handoff_path(run)
    if not path.is_file():
        return None
    lease = read_payload(path)
    if lease.get("state") in {"released", "not_required"}:
        return path
    holders = lease.get("holders") if isinstance(lease.get("holders"), list) else []
    lease["state"] = "releasing"
    lease["release_reason"] = reason
    lease.setdefault("commands", [])
    lease.setdefault("blockers", [])
    write_payload(path, lease)

    try:
        restore_holders(runner, holders, lease, tolerate_failure=False)
    except GpuHandoffError as exc:
        lease["state"] = "release_failed"
        lease["released_at"] = utc_now()
        lease["blockers"] = sorted(set([*lease["blockers"], str(exc)]))
        write_payload(path, lease)
        raise

    lease["state"] = "released"
    lease["released_at"] = utc_now()
    write_payload(path, lease)
    return path


def acquire_holders(
    run: LifecycleRun,
    path: Path,
    *,
    target: dict[str, str],
    excluded_holder: tuple[str, str] | None,
    runner: Runner,
) -> Path:
    nodes = kubectl_json(runner, ["kubectl", "get", "nodes", "-o", "json"])
    allocatable = total_gpu_allocatable(nodes)
    if allocatable < 1:
        raise GpuHandoffError("single_gpu_handoff_no_allocatable_gpu")
    if allocatable > 1:
        write_payload(
            path,
            base_payload(run, target, "not_required")
            | {"gpu_allocatable": allocatable, "reason": "multiple_gpus_available"},
        )
        return path

    holders: list[dict[str, Any]] = []
    holder_blockers: list[str] = []
    for namespace, deployment in configured_holders():
        if excluded_holder == (namespace, deployment):
            continue
        payload = optional_deployment(runner, namespace, deployment)
        if not payload:
            continue
        replicas = int(payload.get("spec", {}).get("replicas") or 0)
        if replicas < 1:
            continue
        images = deployment_images(payload)
        holder = {
            "namespace": namespace,
            "deployment": deployment,
            "original_replicas": replicas,
            "selector": deployment_selector(payload),
            "images": images,
        }
        holders.append(holder)
        available = int(payload.get("status", {}).get("availableReplicas") or 0)
        if available < replicas:
            holder_blockers.append(
                f"gpu_handoff_holder_not_ready:{namespace}/{deployment}"
            )
        for image in images:
            if local_image_required(image) and not node_has_image(nodes, image):
                holder_blockers.append(
                    f"gpu_handoff_holder_image_unavailable:{namespace}/{deployment}:{image}"
                )

    lease = base_payload(run, target, "acquiring") | {
        "gpu_allocatable": allocatable,
        "holders": holders,
        "commands": [],
        "acquired_at": None,
        "released_at": None,
        "release_reason": None,
        "blockers": [],
    }
    write_payload(path, lease)
    if holder_blockers:
        lease["state"] = "acquire_failed"
        lease["blockers"] = sorted(set(holder_blockers))
        write_payload(path, lease)
        raise GpuHandoffError(";".join(lease["blockers"]))
    if not holders:
        lease["state"] = "not_required"
        lease["reason"] = "no_active_configured_gpu_holder"
        write_payload(path, lease)
        return path

    scaled: list[dict[str, Any]] = []
    try:
        for holder in holders:
            scale_command = [
                "kubectl",
                "-n",
                holder["namespace"],
                "scale",
                f"deployment/{holder['deployment']}",
                "--replicas=0",
            ]
            run_checked(runner, scale_command, lease)
            scaled.append(holder)
            wait_for_pods_deleted(runner, holder, lease)
    except GpuHandoffError as exc:
        lease["blockers"].append(str(exc))
        restore_holders(runner, scaled, lease, tolerate_failure=True)
        lease["state"] = "acquire_failed"
        write_payload(path, lease)
        raise

    lease["state"] = "acquired"
    lease["acquired_at"] = utc_now()
    write_payload(path, lease)
    return path


def base_payload(run: LifecycleRun, target: dict[str, str], state: str) -> dict[str, Any]:
    return {
        "schema_version": HANDOFF_SCHEMA,
        "run_id": run.run_id,
        "state": state,
        "target": target,
        "observed_at": utc_now(),
    }


def configured_holders() -> list[tuple[str, str]]:
    configured = os.getenv(
        "EVM_LIFECYCLE_GPU_HOLDERS",
        "evm-production/evm-b0-production",
    )
    holders: list[tuple[str, str]] = []
    for item in configured.split(","):
        namespace, separator, deployment = item.strip().partition("/")
        if separator and namespace and deployment:
            holders.append((namespace, deployment))
    return holders


def optional_deployment(runner: Runner, namespace: str, deployment: str) -> dict[str, Any]:
    result = run_command(
        runner,
        ["kubectl", "-n", namespace, "get", f"deployment/{deployment}", "-o", "json"],
    )
    if result.returncode == 0:
        return parse_json(result.stdout, f"deployment_inventory_invalid:{namespace}/{deployment}")
    if "notfound" in (result.stderr or "").replace(" ", "").lower():
        return {}
    raise GpuHandoffError(f"deployment_inventory_failed:{namespace}/{deployment}")


def kubectl_json(runner: Runner, command: list[str]) -> dict[str, Any]:
    result = run_command(runner, command)
    if result.returncode != 0:
        raise GpuHandoffError(f"kubectl_query_failed:{' '.join(command[1:])}")
    return parse_json(result.stdout, "kubectl_query_invalid")


def parse_json(value: str, error: str) -> dict[str, Any]:
    try:
        payload = json.loads(value)
    except json.JSONDecodeError as exc:
        raise GpuHandoffError(error) from exc
    if not isinstance(payload, dict):
        raise GpuHandoffError(error)
    return payload


def total_gpu_allocatable(payload: dict[str, Any]) -> int:
    total = 0
    for item in payload.get("items", []):
        if not isinstance(item, dict):
            continue
        try:
            total += int(str(item.get("status", {}).get("allocatable", {}).get("nvidia.com/gpu") or "0"))
        except ValueError:
            continue
    return total


def deployment_selector(payload: dict[str, Any]) -> str:
    labels = payload.get("spec", {}).get("selector", {}).get("matchLabels", {})
    if not isinstance(labels, dict):
        return ""
    return ",".join(f"{key}={value}" for key, value in sorted(labels.items()))


def deployment_images(payload: dict[str, Any]) -> list[str]:
    containers = (
        payload.get("spec", {})
        .get("template", {})
        .get("spec", {})
        .get("containers", [])
    )
    if not isinstance(containers, list):
        return []
    return sorted(
        {
            str(container.get("image") or "")
            for container in containers
            if isinstance(container, dict) and container.get("image")
        }
    )


def local_image_required(image: str) -> bool:
    prefix = os.getenv("EVM_LIFECYCLE_LOCAL_IMAGE_PREFIX", "enterprise-vision-mlops-")
    return image.startswith(prefix) and "@sha256:" in image


def node_has_image(nodes: dict[str, Any], image: str) -> bool:
    for node in nodes.get("items", []):
        if not isinstance(node, dict):
            continue
        for item in node.get("status", {}).get("images", []):
            if isinstance(item, dict) and image in item.get("names", []):
                return True
    return False


def wait_for_pods_deleted(runner: Runner, holder: dict[str, Any], lease: dict[str, Any]) -> None:
    selector = str(holder.get("selector") or "")
    if not selector:
        return
    run_checked(
        runner,
        [
            "kubectl",
            "-n",
            str(holder["namespace"]),
            "wait",
            "--for=delete",
            "pod",
            "-l",
            selector,
            "--timeout=120s",
        ],
        lease,
    )


def scale_target_to_zero(
    runner: Runner,
    serving: ServingBundle,
    lease: dict[str, Any],
) -> None:
    command = [
        "kubectl",
        "-n",
        serving.namespace,
        "scale",
        f"deployment/{serving.deployment_name}",
        "--replicas=0",
    ]
    result = run_command(runner, command)
    lease["commands"].append(" ".join(command))
    if result.returncode != 0:
        compact_error = (result.stderr or "").replace(" ", "").lower()
        if "notfound" in compact_error:
            return
        raise GpuHandoffError(f"gpu_handoff_target_scale_failed:{serving.namespace}/{serving.deployment_name}")
    wait_for_pods_deleted(
        runner,
        {
            "namespace": serving.namespace,
            "deployment": serving.deployment_name,
            "selector": f"app.kubernetes.io/name={serving.deployment_name}",
        },
        lease,
    )


def restore_holders(
    runner: Runner,
    holders: list[dict[str, Any]],
    lease: dict[str, Any],
    *,
    tolerate_failure: bool,
) -> None:
    errors: list[str] = []
    for holder in holders:
        namespace = str(holder["namespace"])
        deployment = str(holder["deployment"])
        replicas = int(holder.get("original_replicas") or 0)
        try:
            run_checked(
                runner,
                [
                    "kubectl",
                    "-n",
                    namespace,
                    "scale",
                    f"deployment/{deployment}",
                    f"--replicas={replicas}",
                ],
                lease,
            )
            if replicas:
                run_checked(
                    runner,
                    [
                        "kubectl",
                        "-n",
                        namespace,
                        "rollout",
                        "status",
                        f"deployment/{deployment}",
                        "--timeout=180s",
                    ],
                    lease,
                )
        except GpuHandoffError as exc:
            errors.append(str(exc))
    if errors:
        lease.setdefault("blockers", []).extend(errors)
        if not tolerate_failure:
            raise GpuHandoffError("gpu_handoff_holder_restore_failed:" + ";".join(errors))


def run_checked(runner: Runner, command: list[str], lease: dict[str, Any]) -> None:
    result = run_command(runner, command)
    lease["commands"].append(" ".join(command))
    if result.returncode != 0:
        message = (result.stderr or result.stdout or "kubectl command failed").strip()
        raise GpuHandoffError(f"gpu_handoff_command_failed:{message}")


def run_command(runner: Runner, command: list[str]) -> subprocess.CompletedProcess[str]:
    return runner(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=240,
        check=False,
    )


def read_payload(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def write_payload(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    temporary.replace(path)


def utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
