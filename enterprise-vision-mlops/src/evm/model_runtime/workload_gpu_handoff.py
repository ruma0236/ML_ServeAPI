from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path
from typing import Any

from evm.control_panel.scenario_workload_control import gpu_handoff_request_path
from evm.control_panel.scenario_workloads import atomic_write_json, payload_sha256, utc_now
from evm.model_runtime.common import ModelRuntimeError


def run_command(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def kubectl_json(command: list[str]) -> dict[str, Any]:
    result = run_command(command)
    if result.returncode != 0:
        raise ModelRuntimeError(f"scenario_gpu_handoff_query_failed:{' '.join(command[1:])}")
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise ModelRuntimeError("scenario_gpu_handoff_query_invalid") from exc
    if not isinstance(payload, dict):
        raise ModelRuntimeError("scenario_gpu_handoff_query_invalid")
    return payload


def acquire_workload_gpu_handoff(
    run: Any,
    *,
    timeout_seconds: int = 3600,
) -> Path:
    request_path = gpu_handoff_request_path(run)
    deadline = time.monotonic() + timeout_seconds
    while not request_path.is_file() and time.monotonic() < deadline:
        time.sleep(2)
    if not request_path.is_file():
        raise ModelRuntimeError("scenario_gpu_handoff_approval_timeout")
    try:
        request = json.loads(request_path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        raise ModelRuntimeError("scenario_gpu_handoff_request_invalid") from exc
    expected_material = {
        "run_id": run.run_id,
        "identity_sha256": run.identity.identity_sha256,
        "source_commit": run.identity.source_commit,
        "target": {
            "kind": "Deployment",
            "namespace": "evm-production",
            "name": "evm-b0-production",
        },
        "action": "release_exact_single_gpu_holder_for_scenario_workload",
    }
    if (
        request.get("state") != "approved"
        or request.get("request_digest") != payload_sha256(expected_material)
        or any(request.get(key) != value for key, value in expected_material.items())
        or str(request.get("approver") or "").strip() == run.actor.strip()
    ):
        raise ModelRuntimeError("scenario_gpu_handoff_request_identity_mismatch")

    namespace = "evm-production"
    name = "evm-b0-production"
    deployment = kubectl_json(
        ["kubectl", "-n", namespace, "get", f"deployment/{name}", "-o", "json"]
    )
    uid = str(deployment.get("metadata", {}).get("uid") or "")
    replicas = int(deployment.get("spec", {}).get("replicas") or 0)
    available = int(deployment.get("status", {}).get("availableReplicas") or 0)
    selector = deployment.get("spec", {}).get("selector", {}).get("matchLabels", {})
    if not uid or replicas != 1 or available != 1 or not selector:
        raise ModelRuntimeError(
            "scenario_gpu_handoff_holder_not_exact_ready:"
            f"uid={bool(uid)}:desired={replicas}:available={available}"
        )
    label_selector = ",".join(f"{key}={value}" for key, value in sorted(selector.items()))
    pods = kubectl_json(
        ["kubectl", "-n", namespace, "get", "pods", "-l", label_selector, "-o", "json"]
    )
    active_pods = [
        item
        for item in pods.get("items", [])
        if item.get("status", {}).get("phase") == "Running"
        and not item.get("metadata", {}).get("deletionTimestamp")
    ]
    if len(active_pods) != 1:
        raise ModelRuntimeError(
            f"scenario_gpu_handoff_active_pod_identity_ambiguous:{len(active_pods)}"
        )
    target = {
        "kind": "Deployment",
        "namespace": namespace,
        "name": name,
        "uid": uid,
        "selector": label_selector,
        "pod_uid": active_pods[0].get("metadata", {}).get("uid"),
        "pod_name": active_pods[0].get("metadata", {}).get("name"),
        "original_replicas": 1,
    }
    binding = {
        "run_id": run.run_id,
        "source_commit": run.identity.source_commit,
        "identity_sha256": run.identity.identity_sha256,
        "target": target,
        "action": expected_material["action"],
    }
    evidence_path = request_path.with_name("gpu-handoff-evidence.json")
    evidence: dict[str, Any] = {
        "schema_version": "evm.scenario_workload_gpu_handoff.v1",
        **binding,
        "action_digest": payload_sha256(binding),
        "approver": request["approver"],
        "request_digest": request["request_digest"],
        "state": "acquiring",
        "commands": [],
        "acquired_at": None,
        "released_at": None,
        "blockers": [],
    }
    atomic_write_json(evidence_path, evidence)
    command = ["kubectl", "-n", namespace, "scale", f"deployment/{name}", "--replicas=0"]
    result = run_command(command)
    evidence["commands"].append(" ".join(command))
    if result.returncode != 0:
        evidence["state"] = "acquire_failed"
        evidence["blockers"] = ["scenario_gpu_handoff_scale_down_failed"]
        atomic_write_json(evidence_path, evidence)
        raise ModelRuntimeError("scenario_gpu_handoff_scale_down_failed")
    try:
        wait_for_pod_count(namespace, label_selector, expected=0, timeout_seconds=180)
    except Exception as exc:
        evidence["state"] = "acquire_failed_restoring"
        evidence["blockers"] = ["scenario_gpu_handoff_scale_down_not_converged"]
        atomic_write_json(evidence_path, evidence)
        restore = run_command(
            ["kubectl", "-n", namespace, "scale", f"deployment/{name}", "--replicas=1"]
        )
        evidence["commands"].append(
            f"kubectl -n {namespace} scale deployment/{name} --replicas=1"
        )
        if restore.returncode == 0:
            try:
                wait_for_pod_count(
                    namespace,
                    label_selector,
                    expected=1,
                    timeout_seconds=300,
                    require_ready=True,
                )
                evidence["state"] = "acquire_failed_restored"
                evidence["restored_at"] = utc_now()
            except Exception:
                evidence["state"] = "acquire_failed_restore_unconfirmed"
                evidence["blockers"].append("scenario_gpu_handoff_restore_unconfirmed")
        else:
            evidence["state"] = "acquire_failed_restore_command_failed"
            evidence["blockers"].append("scenario_gpu_handoff_restore_command_failed")
        atomic_write_json(evidence_path, evidence)
        raise ModelRuntimeError(
            f"scenario_gpu_handoff_scale_down_not_converged:{type(exc).__name__}"
        ) from exc
    evidence["state"] = "acquired"
    evidence["acquired_at"] = utc_now()
    request["state"] = "consumed"
    request["consumed_at"] = evidence["acquired_at"]
    request["target_uid"] = uid
    request["action_digest"] = evidence["action_digest"]
    atomic_write_json(request_path, request)
    atomic_write_json(evidence_path, evidence)
    return evidence_path


def release_workload_gpu_handoff(run: Any, path: Path, *, reason: str) -> Path:
    evidence = json.loads(path.read_text(encoding="utf-8-sig"))
    if evidence.get("state") == "released":
        return path
    target = evidence.get("target") or {}
    namespace = str(target.get("namespace") or "")
    name = str(target.get("name") or "")
    uid = str(target.get("uid") or "")
    selector = str(target.get("selector") or "")
    deployment = kubectl_json(
        ["kubectl", "-n", namespace, "get", f"deployment/{name}", "-o", "json"]
    )
    if str(deployment.get("metadata", {}).get("uid") or "") != uid:
        evidence["state"] = "release_failed"
        evidence.setdefault("blockers", []).append("scenario_gpu_handoff_restore_uid_mismatch")
        atomic_write_json(path, evidence)
        raise ModelRuntimeError("scenario_gpu_handoff_restore_uid_mismatch")
    command = ["kubectl", "-n", namespace, "scale", f"deployment/{name}", "--replicas=1"]
    result = run_command(command)
    evidence.setdefault("commands", []).append(" ".join(command))
    if result.returncode != 0:
        evidence["state"] = "release_failed"
        evidence.setdefault("blockers", []).append("scenario_gpu_handoff_scale_up_failed")
        atomic_write_json(path, evidence)
        raise ModelRuntimeError("scenario_gpu_handoff_scale_up_failed")
    wait_for_pod_count(namespace, selector, expected=1, timeout_seconds=300, require_ready=True)
    evidence["state"] = "released"
    evidence["released_at"] = utc_now()
    evidence["release_reason"] = reason
    atomic_write_json(path, evidence)
    return path


def wait_for_pod_count(
    namespace: str,
    selector: str,
    *,
    expected: int,
    timeout_seconds: int,
    require_ready: bool = False,
) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        payload = kubectl_json(
            ["kubectl", "-n", namespace, "get", "pods", "-l", selector, "-o", "json"]
        )
        active = [
            item
            for item in payload.get("items", [])
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
    raise ModelRuntimeError(
        f"scenario_gpu_handoff_pod_wait_timeout:expected={expected}:ready={require_ready}"
    )
