from __future__ import annotations

import json
import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable, Literal

from evm.control_panel.lifecycle_kubernetes import ServingBundle, TrainingBundle
from evm.control_panel.lifecycle_runs import LifecycleRun
from evm.operations.failure_scenarios import (
    ApprovalRejected,
    ApprovalStore,
    TargetRef,
)


Runner = Callable[..., subprocess.CompletedProcess[str]]
HANDOFF_SCHEMA = "evm.lifecycle_gpu_handoff.v1"
HANDOFF_APPROVAL_SCHEMA = "evm.lifecycle_gpu_handoff_approval_reference.v1"
GpuHandoffPhase = Literal["training", "isolated_ct", "staging_deployment"]


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


def handoff_approval_root(run: LifecycleRun, phase: GpuHandoffPhase) -> Path:
    return Path(run.artifact_root) / "kubernetes" / "handoff_approvals" / phase


def handoff_approval_reference_path(
    run: LifecycleRun,
    phase: GpuHandoffPhase,
) -> Path:
    return handoff_approval_root(run, phase) / "approval-reference.json"


def handoff_action(phase: GpuHandoffPhase) -> str:
    return f"release_single_gpu_holder_for_{phase}"


def training_handoff_phase(training: TrainingBundle) -> GpuHandoffPhase:
    return "isolated_ct" if "-ct-" in training.job_name else "training"


def issue_gpu_handoff_approval(
    run: LifecycleRun,
    *,
    phase: GpuHandoffPhase,
    approver: str,
    reason: str,
    ttl_seconds: int,
    runner: Runner,
) -> Path:
    source_revision = str(run.source_commit or "").strip()
    if len(source_revision) != 40:
        raise GpuHandoffError("gpu_handoff_source_revision_missing")
    if ttl_seconds < 60:
        raise GpuHandoffError("gpu_handoff_approval_ttl_too_short")

    active: list[TargetRef] = []
    for namespace, deployment in configured_holders():
        payload = optional_deployment(runner, namespace, deployment)
        if int(payload.get("spec", {}).get("replicas") or 0) < 1:
            continue
        uid = str(payload.get("metadata", {}).get("uid") or "").strip()
        if not uid:
            raise GpuHandoffError(
                f"gpu_handoff_holder_uid_missing:{namespace}/{deployment}"
            )
        active.append(TargetRef(namespace=namespace, name=deployment, uid=uid))
    if len(active) != 1:
        raise GpuHandoffError(
            f"gpu_handoff_exact_holder_required:actual={len(active)}"
        )

    store = ApprovalStore(handoff_approval_root(run, phase))
    binding = store.issue(
        run_id=run.run_id,
        target=active[0],
        action=handoff_action(phase),
        source_revision=source_revision,
        approver=approver,
        ttl_seconds=ttl_seconds,
    )
    reference = handoff_approval_reference_path(run, phase)
    write_payload(
        reference,
        {
            "schema_version": HANDOFF_APPROVAL_SCHEMA,
            "run_id": run.run_id,
            "phase": phase,
            "approval_id": binding.approval_id,
            "approval_path": str(store.root / f"{binding.approval_id}.json"),
            "action": binding.action,
            "action_digest": binding.action_digest,
            "target": binding.target.model_dump(mode="json"),
            "source_revision": binding.source_revision,
            "approver": binding.approver,
            "reason": reason,
            "issued_at": binding.issued_at.isoformat(),
            "expires_at": binding.expires_at.isoformat(),
            "single_use": True,
            "state": "approved",
        },
    )
    return reference


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
        phase="staging_deployment",
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
        phase=training_handoff_phase(training),
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
    phase: GpuHandoffPhase,
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
            "uid": str(payload.get("metadata", {}).get("uid") or ""),
            "original_replicas": replicas,
            "selector": deployment_selector(payload),
            "images": images,
        }
        active_pods = active_deployment_pods(
            runner,
            namespace=namespace,
            selector=str(holder["selector"]),
        )
        holder["active_pods"] = active_pods
        holders.append(holder)
        if not holder["uid"]:
            holder_blockers.append(
                f"gpu_handoff_holder_uid_missing:{namespace}/{deployment}"
            )
        available = int(payload.get("status", {}).get("availableReplicas") or 0)
        if available < replicas:
            holder_blockers.append(
                f"gpu_handoff_holder_not_ready:{namespace}/{deployment}"
            )
        if len(active_pods) != replicas:
            holder_blockers.append(
                "gpu_handoff_holder_active_pod_identity_ambiguous:"
                f"{namespace}/{deployment}:expected={replicas}:actual={len(active_pods)}"
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

    try:
        approval = consume_gpu_handoff_approval(run, phase, holders)
    except GpuHandoffError as exc:
        lease["state"] = "acquire_failed"
        lease["blockers"] = sorted(set([*lease["blockers"], str(exc)]))
        write_payload(path, lease)
        raise
    lease["approval"] = approval
    write_payload(path, lease)

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


def consume_gpu_handoff_approval(
    run: LifecycleRun,
    phase: GpuHandoffPhase,
    holders: list[dict[str, Any]],
) -> dict[str, Any]:
    if len(holders) != 1:
        raise GpuHandoffError(
            f"gpu_handoff_exact_holder_required:actual={len(holders)}"
        )
    reference_path = handoff_approval_reference_path(run, phase)
    reference = read_payload(reference_path)
    if reference.get("schema_version") != HANDOFF_APPROVAL_SCHEMA:
        raise GpuHandoffError(f"gpu_handoff_approval_missing:{phase}")
    if reference.get("run_id") != run.run_id or reference.get("phase") != phase:
        raise GpuHandoffError(f"gpu_handoff_approval_reference_mismatch:{phase}")

    holder = holders[0]
    target = TargetRef(
        namespace=str(holder["namespace"]),
        name=str(holder["deployment"]),
        uid=str(holder["uid"]),
    )
    source_revision = str(run.source_commit or "").strip()
    store = ApprovalStore(handoff_approval_root(run, phase))
    approval_id = str(reference.get("approval_id") or "")
    try:
        binding = store.consume(
            approval_id,
            run_id=run.run_id,
            target=target,
            action=handoff_action(phase),
            source_revision=source_revision,
        )
    except (ApprovalRejected, OSError, ValueError) as exc:
        raise GpuHandoffError(f"gpu_handoff_approval_rejected:{phase}:{exc}") from exc

    consumed_at = utc_now()
    reference.update(
        {
            "state": "consumed",
            "consumed_at": consumed_at,
            "consumed_receipt_path": str(
                store.root / f"{binding.approval_id}.consumed.json"
            ),
        }
    )
    write_payload(reference_path, reference)
    return {
        "approval_id": binding.approval_id,
        "action_digest": binding.action_digest,
        "target_uid": binding.target.uid,
        "source_revision": binding.source_revision,
        "approver": binding.approver,
        "expires_at": binding.expires_at.isoformat(),
        "single_use": True,
        "consumed_at": consumed_at,
        "reference_path": str(reference_path),
    }


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


def active_deployment_pods(
    runner: Runner,
    *,
    namespace: str,
    selector: str,
) -> list[dict[str, str]]:
    if not selector:
        raise GpuHandoffError(
            f"gpu_handoff_holder_selector_missing:{namespace}"
        )
    payload = kubectl_json(
        runner,
        ["kubectl", "-n", namespace, "get", "pods", "-l", selector, "-o", "json"],
    )
    active: list[dict[str, str]] = []
    for item in payload.get("items", []):
        if not isinstance(item, dict):
            continue
        metadata = item.get("metadata", {})
        status = item.get("status", {})
        if metadata.get("deletionTimestamp"):
            continue
        if str(status.get("phase") or "") in {"Failed", "Succeeded"}:
            continue
        name = str(metadata.get("name") or "").strip()
        uid = str(metadata.get("uid") or "").strip()
        if not name or not uid:
            raise GpuHandoffError(
                f"gpu_handoff_active_pod_identity_missing:{namespace}"
            )
        active.append({"name": name, "uid": uid})
    return sorted(active, key=lambda item: (item["name"], item["uid"]))


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
    active_pods = holder.get("active_pods")
    if not isinstance(active_pods, list):
        raise GpuHandoffError("gpu_handoff_active_pod_identity_missing")
    for pod in active_pods:
        if not isinstance(pod, dict) or not pod.get("name") or not pod.get("uid"):
            raise GpuHandoffError("gpu_handoff_active_pod_identity_missing")
        run_checked(
            runner,
            [
                "kubectl",
                "-n",
                str(holder["namespace"]),
                "wait",
                "--for=delete",
                f"pod/{pod['name']}",
                "--timeout=120s",
            ],
            lease,
        )


def scale_target_to_zero(
    runner: Runner,
    serving: ServingBundle,
    lease: dict[str, Any],
) -> None:
    deployment = optional_deployment(runner, serving.namespace, serving.deployment_name)
    target = {
        "namespace": serving.namespace,
        "deployment": serving.deployment_name,
        "active_pods": [],
    }
    if deployment:
        target["active_pods"] = active_deployment_pods(
            runner,
            namespace=serving.namespace,
            selector=deployment_selector(deployment),
        )
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
        target,
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
