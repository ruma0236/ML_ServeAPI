from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import requests

from evm.control_panel.scenario_workload_control import get_preset
from evm.control_panel.scenario_workload_production import (
    ScenarioProductionIntent,
    canonical_intent_evidence_path,
    get_production_intent,
    list_production_intents,
    transition_production_intent,
    validate_intent_identity,
)
from evm.control_panel.scenario_workloads import (
    atomic_write_json,
    file_sha256,
    get_workload_run,
    workload_artifact_path,
)
from evm.model_runtime.common import ModelRuntimeError, read_jsonl, split_records, utc_now
from evm.model_runtime.workload_gpu_handoff import kubectl_json, run_command, wait_for_pod_count


PROMETHEUS_TARGET_PATH = Path(
    "F:/EnterpriseMLOps_Data/enterprise-vision-mlops/"
    "artifacts/w7/prometheus-targets/lifecycle-serving.json"
)
PROMETHEUS_URI = "http://127.0.0.1:9090"


def apply_production_intent(intent_id: str) -> ScenarioProductionIntent:
    intent = get_production_intent(intent_id)
    if intent.state != "queued":
        raise ModelRuntimeError(f"scenario_production_intent_not_queued:{intent.state}")
    try:
        validate_intent_identity(intent)
        run = get_workload_run(intent.run_id)
        preset = get_preset(intent.preset_id)
        if not port_available(intent.target.port):
            raise ModelRuntimeError("scenario_production_port_unavailable")
    except Exception as exc:
        return fail_intent(intent, f"scenario_production_admission_failed:{error_detail(exc)}")
    transition_production_intent(
        intent_id,
        expected_state="queued",
        state="applying",
        actor="scenario-workload-worker",
        event="scenario_production_apply_started",
    )

    evidence_path = workload_artifact_path(canonical_intent_evidence_path(intent_id))
    evidence_path.parent.mkdir(parents=True, exist_ok=True)
    target_backup = PROMETHEUS_TARGET_PATH.read_bytes() if PROMETHEUS_TARGET_PATH.is_file() else None
    server: subprocess.Popen[str] | None = None
    server_started_at: str | None = None
    holder: dict[str, str] | None = None
    evidence: dict[str, Any] = {
        "schema_version": "evm.scenario_production_deployment_evidence.v1",
        "intent_id": intent.intent_id,
        "run_id": run.run_id,
        "action_digest": intent.action_digest,
        "source_commit": intent.source_commit,
        "identity_sha256": intent.identity_sha256,
        "model_artifact_sha256": intent.model_artifact_sha256,
        "target": intent.target.model_dump(mode="json"),
        "state": "applying",
        "steps": [],
        "blockers": [],
        "started_at": utc_now(),
    }
    atomic_write_json(evidence_path, evidence)
    try:
        holder = exact_gpu_holder()
        evidence["gpu_holder"] = holder
        evidence["steps"].append({"name": "exact_gpu_holder_preflight", "status": "pass"})
        atomic_write_json(evidence_path, evidence)

        scale_holder(holder, replicas=0, require_ready=False)
        evidence["steps"].append({"name": "release_b0_gpu_holder", "status": "pass"})
        atomic_write_json(evidence_path, evidence)

        server, process_started_at, command = start_production_server(intent, preset.model_dir)
        server_started_at = process_started_at
        evidence["steps"].append(
            {
                "name": "start_local_production_cuda_service",
                "status": "pass",
                "pid": server.pid,
                "process_started_at": process_started_at,
                "command": command,
            }
        )
        ready = wait_for_ready(intent, server)
        evidence["ready"] = ready
        evidence["steps"].append({"name": "exact_ready_identity", "status": "pass"})

        inference = verify_production_inference(intent)
        evidence["inference"] = inference
        evidence["steps"].append({"name": "real_cuda_inference", "status": "pass"})

        write_prometheus_target(intent)
        prometheus = wait_for_prometheus(intent)
        evidence["prometheus"] = prometheus
        evidence["steps"].append({"name": "prometheus_target_up", "status": "pass"})
        evidence["state"] = "applied"
        evidence["applied_at"] = utc_now()
        atomic_write_json(evidence_path, evidence)
        return transition_production_intent(
            intent_id,
            expected_state="applying",
            state="applied",
            actor="scenario-workload-worker",
            event="scenario_production_apply_completed",
            updates={
                "applied_at": evidence["applied_at"],
                "service_pid": server.pid,
                "service_process_started_at": process_started_at,
                "gpu_holder_uid": holder["uid"],
                "evidence_uri": str(canonical_intent_evidence_path(intent_id)),
                "blockers": [],
            },
        )
    except Exception as exc:
        evidence["state"] = "failed_restoring"
        evidence["blockers"] = [str(exc)]
        evidence["failed_at"] = utc_now()
        atomic_write_json(evidence_path, evidence)
        if server is not None:
            stop_bound_process(
                server.pid,
                intent,
                expected_started_at=server_started_at,
                allow_missing=True,
            )
        restore_target(target_backup)
        restore_blocker = None
        if holder is not None:
            try:
                scale_holder(holder, replicas=1, require_ready=True)
                evidence["steps"].append({"name": "restore_b0_gpu_holder", "status": "pass"})
            except Exception as restore_exc:
                restore_blocker = f"scenario_production_restore_failed:{restore_exc}"
                evidence["blockers"].append(restore_blocker)
        evidence["state"] = "failed" if restore_blocker is None else "failed_restore_unconfirmed"
        atomic_write_json(evidence_path, evidence)
        return fail_intent(intent, *evidence["blockers"], evidence_uri=str(canonical_intent_evidence_path(intent_id)))


def rollback_production_intent(intent_id: str) -> ScenarioProductionIntent:
    intent = get_production_intent(intent_id)
    if intent.state != "rollback_requested":
        raise ModelRuntimeError(f"scenario_production_rollback_not_requested:{intent.state}")
    transition_production_intent(
        intent_id,
        expected_state="rollback_requested",
        state="rolling_back",
        actor="scenario-workload-worker",
        event="scenario_production_rollback_started",
    )
    evidence_path = workload_artifact_path(canonical_intent_evidence_path(intent_id))
    evidence = read_json(evidence_path)
    try:
        if intent.service_pid is None:
            raise ModelRuntimeError("scenario_production_service_pid_missing")
        if process_matches(intent.service_pid, intent):
            stop_exact_process(intent.service_pid, intent)
        elif not port_available(intent.target.port):
            raise ModelRuntimeError("scenario_production_rollback_unknown_port_owner")
        restore_target_from_evidence_dir(evidence_path.parent)
        holder = evidence.get("gpu_holder")
        if not isinstance(holder, dict) or holder.get("uid") != intent.gpu_holder_uid:
            raise ModelRuntimeError("scenario_production_rollback_holder_identity_missing")
        scale_holder({str(key): str(value) for key, value in holder.items()}, replicas=1, require_ready=True)
        evidence["state"] = "rolled_back"
        evidence["rolled_back_at"] = utc_now()
        evidence.setdefault("steps", []).append({"name": "rollback_to_b0", "status": "pass"})
        atomic_write_json(evidence_path, evidence)
        return transition_production_intent(
            intent_id,
            expected_state="rolling_back",
            state="rolled_back",
            actor="scenario-workload-worker",
            event="scenario_production_rollback_completed",
            updates={"rolled_back_at": evidence["rolled_back_at"]},
        )
    except Exception as exc:
        return transition_production_intent(
            intent_id,
            expected_state="rolling_back",
            state="failed",
            actor="scenario-workload-worker",
            event="scenario_production_rollback_failed",
            updates={"blockers": [str(exc)]},
        )


def reconcile_applied_intent(intent: ScenarioProductionIntent) -> ScenarioProductionIntent:
    if intent.state != "applied":
        return intent
    try:
        if intent.service_pid is None or not process_matches(intent.service_pid, intent):
            raise ModelRuntimeError("scenario_production_service_identity_lost")
        response = requests.get(f"{intent.target.endpoint}/ready", timeout=4)
        payload = response.json()
        if response.status_code != 200 or not ready_identity_matches(intent, payload):
            raise ModelRuntimeError("scenario_production_ready_identity_lost")
        return intent
    except Exception as exc:
        requested = transition_production_intent(
            intent.intent_id,
            expected_state="applied",
            state="rollback_requested",
            actor="scenario-workload-worker",
            event="scenario_production_health_recovery_requested",
            updates={"blockers": [str(exc)]},
        )
        return rollback_production_intent(requested.intent_id)


def recover_incomplete_production_intents() -> list[ScenarioProductionIntent]:
    recovered: list[ScenarioProductionIntent] = []
    for intent in list_production_intents(limit=500).intents:
        if intent.state not in {"applying", "rolling_back"}:
            continue
        evidence_path = workload_artifact_path(canonical_intent_evidence_path(intent.intent_id))
        evidence = read_json(evidence_path)
        blockers = ["scenario_production_interrupted_worker_recovery"]
        try:
            steps = evidence.get("steps")
            process_step = next(
                (
                    item
                    for item in steps
                    if isinstance(item, dict)
                    and item.get("name") == "start_local_production_cuda_service"
                ),
                None,
            ) if isinstance(steps, list) else None
            if isinstance(process_step, dict) and process_step.get("pid"):
                stop_bound_process(
                    int(process_step["pid"]),
                    intent,
                    expected_started_at=str(process_step.get("process_started_at") or "") or None,
                    allow_missing=True,
                )
            restore_target_from_evidence_dir(evidence_path.parent)
            holder = evidence.get("gpu_holder")
            if isinstance(holder, dict) and holder.get("uid"):
                normalized = {str(key): str(value) for key, value in holder.items()}
                current = kubectl_json(
                    [
                        "kubectl",
                        "-n",
                        normalized["namespace"],
                        "get",
                        f"deployment/{normalized['name']}",
                        "-o",
                        "json",
                    ]
                )
                if str(current.get("metadata", {}).get("uid") or "") != normalized["uid"]:
                    raise ModelRuntimeError("scenario_production_recovery_holder_uid_changed")
                desired = int(current.get("spec", {}).get("replicas") or 0)
                available = int(current.get("status", {}).get("availableReplicas") or 0)
                if desired != 1 or available != 1:
                    scale_holder(normalized, replicas=1, require_ready=True)
            evidence["state"] = "failed_recovered"
            evidence["recovered_at"] = utc_now()
            evidence["blockers"] = blockers
            atomic_write_json(evidence_path, evidence)
        except Exception as exc:
            blockers.append(f"scenario_production_interrupted_recovery_failed:{exc}")
            evidence["state"] = "failed_recovery_unconfirmed"
            evidence["recovered_at"] = utc_now()
            evidence["blockers"] = blockers
            atomic_write_json(evidence_path, evidence)
        recovered.append(
            transition_production_intent(
                intent.intent_id,
                expected_state=intent.state,
                state="failed",
                actor="scenario-workload-worker",
                event="scenario_production_interrupted_state_reconciled",
                updates={"blockers": blockers, "evidence_uri": str(canonical_intent_evidence_path(intent.intent_id))},
            )
        )
    return recovered


def exact_gpu_holder() -> dict[str, str]:
    namespace = "evm-production"
    name = "evm-b0-production"
    deployment = kubectl_json(
        ["kubectl", "-n", namespace, "get", f"deployment/{name}", "-o", "json"]
    )
    uid = str(deployment.get("metadata", {}).get("uid") or "")
    desired = int(deployment.get("spec", {}).get("replicas") or 0)
    available = int(deployment.get("status", {}).get("availableReplicas") or 0)
    labels = deployment.get("spec", {}).get("selector", {}).get("matchLabels", {})
    if not uid or desired != 1 or available != 1 or not labels:
        raise ModelRuntimeError(
            f"scenario_production_gpu_holder_not_ready:uid={bool(uid)}:desired={desired}:available={available}"
        )
    selector = ",".join(f"{key}={value}" for key, value in sorted(labels.items()))
    pods = kubectl_json(
        ["kubectl", "-n", namespace, "get", "pods", "-l", selector, "-o", "json"]
    )
    active = [
        item
        for item in pods.get("items", [])
        if item.get("status", {}).get("phase") == "Running"
        and not item.get("metadata", {}).get("deletionTimestamp")
        and any(
            condition.get("type") == "Ready" and condition.get("status") == "True"
            for condition in item.get("status", {}).get("conditions", [])
        )
    ]
    if len(active) != 1:
        raise ModelRuntimeError(f"scenario_production_gpu_holder_pod_ambiguous:{len(active)}")
    return {
        "namespace": namespace,
        "name": name,
        "uid": uid,
        "selector": selector,
        "pod_name": str(active[0].get("metadata", {}).get("name") or ""),
        "pod_uid": str(active[0].get("metadata", {}).get("uid") or ""),
    }


def scale_holder(holder: dict[str, str], *, replicas: int, require_ready: bool) -> None:
    current = kubectl_json(
        [
            "kubectl",
            "-n",
            holder["namespace"],
            "get",
            f"deployment/{holder['name']}",
            "-o",
            "json",
        ]
    )
    if str(current.get("metadata", {}).get("uid") or "") != holder["uid"]:
        raise ModelRuntimeError("scenario_production_gpu_holder_uid_changed")
    command = [
        "kubectl",
        "-n",
        holder["namespace"],
        "scale",
        f"deployment/{holder['name']}",
        f"--replicas={replicas}",
    ]
    result = run_command(command)
    if result.returncode != 0:
        raise ModelRuntimeError(f"scenario_production_gpu_holder_scale_failed:{replicas}")
    wait_for_pod_count(
        holder["namespace"],
        holder["selector"],
        expected=replicas,
        timeout_seconds=300,
        require_ready=require_ready,
    )


def start_production_server(
    intent: ScenarioProductionIntent,
    model_dir: str,
) -> tuple[subprocess.Popen[str], str, list[str]]:
    log_path = workload_artifact_path(canonical_intent_evidence_path(intent.intent_id)).with_name(
        "production-serving.log"
    )
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_handle = log_path.open("w", encoding="utf-8")
    command = [
        sys.executable,
        "-m",
        "evm.model_runtime.serving",
        "--model-family",
        intent.model_family,
        "--base-model-dir",
        model_dir,
        "--adapter-dir",
        str(Path(intent.model_artifact_uri).parent),
        "--model-repository",
        intent.model_repository,
        "--model-revision",
        intent.model_revision,
        "--model-artifact-sha256",
        intent.model_artifact_sha256,
        "--data-identity-sha256",
        get_workload_run(intent.run_id).identity.data_identity_sha256,
        "--source-commit",
        intent.source_commit,
        "--lifecycle-run-id",
        intent.run_id,
        "--quantization",
        get_workload_run(intent.run_id).quantization_observed or "none",
        "--environment",
        "local-production",
        "--port",
        str(intent.target.port),
    ]
    creationflags = 0
    if os.name == "nt":
        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
    process = subprocess.Popen(
        command,
        cwd=Path(__file__).resolve().parents[3],
        stdout=log_handle,
        stderr=subprocess.STDOUT,
        text=True,
        creationflags=creationflags,
    )
    process_started_at = process_identity(process.pid)["process_started_at"]
    log_handle.close()
    return process, process_started_at, command


def wait_for_ready(intent: ScenarioProductionIntent, process: subprocess.Popen[str]) -> dict[str, Any]:
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise ModelRuntimeError(f"scenario_production_server_exited:{process.returncode}")
        try:
            response = requests.get(f"{intent.target.endpoint}/ready", timeout=3)
            payload = response.json()
            if response.status_code == 200 and ready_identity_matches(intent, payload):
                return payload
        except (requests.RequestException, ValueError):
            pass
        time.sleep(1)
    raise ModelRuntimeError("scenario_production_ready_timeout")


def ready_identity_matches(intent: ScenarioProductionIntent, payload: dict[str, Any]) -> bool:
    expected = {
        "status": "ready",
        "environment": "local-production",
        "model_family": intent.model_family,
        "model_repository": intent.model_repository,
        "model_revision": intent.model_revision,
        "model_artifact_sha256": intent.model_artifact_sha256,
        "source_commit": intent.source_commit,
        "lifecycle_run_id": intent.run_id,
    }
    return all(payload.get(key) == value for key, value in expected.items())


def verify_production_inference(intent: ScenarioProductionIntent) -> dict[str, Any]:
    run = get_workload_run(intent.run_id)
    record = split_records(read_jsonl(Path(run.identity.manifest_uri)))["test"][0]
    if intent.model_family == "vlm":
        request = {
            "model_family": "vlm",
            "image_uri": record["image_uri"],
            "image_sha256": record["image_sha256"],
            "question": record["question"],
            "choices": record["choices"],
            "max_new_tokens": 8,
        }
    else:
        request = {
            "model_family": "llm",
            "instruction": record["instruction"],
            "context": record.get("context") or None,
            "max_new_tokens": 64,
        }
    response = requests.post(f"{intent.target.endpoint}/infer", json=request, timeout=90)
    payload = response.json()
    if (
        response.status_code != 200
        or payload.get("model_artifact_sha256") != intent.model_artifact_sha256
        or not str(payload.get("output") or "").strip()
    ):
        raise ModelRuntimeError("scenario_production_inference_validation_failed")
    ready = requests.get(f"{intent.target.endpoint}/ready", timeout=10).json()
    if not ready.get("runtime", {}).get("cuda_available"):
        raise ModelRuntimeError("scenario_production_cuda_not_observed")
    return {
        "status": "pass",
        "sample_id": record["sample_id"],
        "response": payload,
        "gpu": ready.get("gpu"),
        "observed_at": utc_now(),
    }


def write_prometheus_target(intent: ScenarioProductionIntent) -> None:
    root = workload_artifact_path(canonical_intent_evidence_path(intent.intent_id)).parent
    root.mkdir(parents=True, exist_ok=True)
    backup = root / "prometheus-target-backup.json"
    backup.write_bytes(PROMETHEUS_TARGET_PATH.read_bytes() if PROMETHEUS_TARGET_PATH.is_file() else b"[]")
    atomic_write_json(
        PROMETHEUS_TARGET_PATH,
        [
            {
                "targets": [f"host.docker.internal:{intent.target.port}"],
                "labels": {
                    "evm_run_id": intent.run_id,
                    "evm_model_family": intent.model_family,
                    "evm_environment": "local-production",
                    "evm_intent_id": intent.intent_id,
                },
            }
        ],
    )


def wait_for_prometheus(intent: ScenarioProductionIntent) -> dict[str, Any]:
    query = (
        'up{job="evm-lifecycle-serving",evm_run_id="'
        f'{intent.run_id}",evm_environment="local-production"'
        "}"
    )
    deadline = time.monotonic() + 75
    last: dict[str, Any] = {}
    while time.monotonic() < deadline:
        response = requests.get(
            f"{PROMETHEUS_URI}/api/v1/query", params={"query": query}, timeout=10
        )
        if response.status_code == 200:
            last = response.json()
            results = last.get("data", {}).get("result", [])
            if any(item.get("value", [None, "0"])[1] == "1" for item in results):
                return {"status": "pass", "query": query, "result": results, "observed_at": utc_now()}
        time.sleep(2)
    raise ModelRuntimeError(f"scenario_production_prometheus_not_up:{json.dumps(last)}")


def process_identity(pid: int) -> dict[str, str]:
    command = (
        "$p=Get-CimInstance Win32_Process -Filter \"ProcessId = "
        f"{int(pid)}\"; if($p){{$p | Select-Object ProcessId,CreationDate,CommandLine | ConvertTo-Json -Compress}}"
    )
    result = subprocess.run(
        ["powershell", "-NoProfile", "-Command", command],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode != 0 or not result.stdout.strip():
        raise ModelRuntimeError("scenario_production_process_identity_unavailable")
    payload = json.loads(result.stdout)
    return {
        "pid": str(payload["ProcessId"]),
        "process_started_at": str(payload["CreationDate"]),
        "command_line": str(payload.get("CommandLine") or ""),
    }


def process_matches(pid: int, intent: ScenarioProductionIntent) -> bool:
    try:
        identity = process_identity(pid)
    except (ModelRuntimeError, ValueError, KeyError):
        return False
    command = identity["command_line"]
    return (
        "evm.model_runtime.serving" in command
        and f"--lifecycle-run-id {intent.run_id}" in command
        and f"--port {intent.target.port}" in command
        and identity["process_started_at"] == intent.service_process_started_at
    )


def stop_exact_process(pid: int, intent: ScenarioProductionIntent, *, allow_missing: bool = False) -> None:
    stop_bound_process(
        pid,
        intent,
        expected_started_at=intent.service_process_started_at,
        allow_missing=allow_missing,
    )


def stop_bound_process(
    pid: int,
    intent: ScenarioProductionIntent,
    *,
    expected_started_at: str | None,
    allow_missing: bool = False,
) -> None:
    try:
        identity = process_identity(pid)
    except (ModelRuntimeError, ValueError, KeyError):
        if allow_missing:
            return
        raise ModelRuntimeError("scenario_production_process_identity_mismatch")
    command = identity["command_line"]
    matches = (
        expected_started_at is not None
        and identity["process_started_at"] == expected_started_at
        and "evm.model_runtime.serving" in command
        and f"--lifecycle-run-id {intent.run_id}" in command
        and f"--port {intent.target.port}" in command
    )
    if not matches:
        if allow_missing:
            return
        raise ModelRuntimeError("scenario_production_process_identity_mismatch")
    result = run_command(["taskkill", "/PID", str(pid), "/T", "/F"])
    if result.returncode != 0 and not allow_missing:
        raise ModelRuntimeError("scenario_production_process_stop_failed")


def restore_target(payload: bytes | None) -> None:
    PROMETHEUS_TARGET_PATH.parent.mkdir(parents=True, exist_ok=True)
    PROMETHEUS_TARGET_PATH.write_bytes(payload if payload is not None else b"[]")


def restore_target_from_evidence_dir(root: Path) -> None:
    backup = root / "prometheus-target-backup.json"
    restore_target(backup.read_bytes() if backup.is_file() else b"[]")


def fail_intent(
    intent: ScenarioProductionIntent,
    *blockers: str,
    evidence_uri: str | None = None,
) -> ScenarioProductionIntent:
    current = get_production_intent(intent.intent_id)
    expected = current.state
    if expected not in {"queued", "applying"}:
        return current
    return transition_production_intent(
        intent.intent_id,
        expected_state=expected,
        state="failed",
        actor="scenario-workload-worker",
        event="scenario_production_apply_failed",
        updates={
            "blockers": sorted(set(blockers)),
            **({"evidence_uri": evidence_uri} if evidence_uri else {}),
        },
    )


def port_available(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        try:
            probe.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


def read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
        return payload if isinstance(payload, dict) else {}
    except (OSError, ValueError):
        return {}


def error_detail(exc: Exception) -> str:
    code = str(getattr(exc, "code", "") or "").strip()
    detail = str(exc).strip()
    return ":".join(value for value in (code, detail) if value) or type(exc).__name__
