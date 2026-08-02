from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from evm.control_panel.lifecycle_kubernetes import short_run_id
from evm.control_panel.lifecycle_runs import get_lifecycle_run
from evm.control_panel.operations import read_tasks, update_task_runtime
from evm.control_panel.schemas import TaskAssignment


Runner = Callable[..., subprocess.CompletedProcess[str]]
ProgressCallback = Callable[[dict[str, object]], None]


class KubernetesTaskExecutionError(RuntimeError):
    pass


class KubernetesTaskCancellationRequested(RuntimeError):
    pass


@dataclass(frozen=True)
class KubernetesTaskObservation:
    runtime_id: str
    resource_uid: str
    lifecycle_run_label: str
    candidate_label: str
    observed_state: str
    evidence_uri: str


def project_root() -> Path:
    configured = os.getenv("EVM_PROJECT_ROOT", "").strip()
    return Path(configured).resolve() if configured else Path(__file__).resolve().parents[3]


def resolve_manifest_dir(value: object) -> Path:
    raw = str(value or "").strip()
    if not raw:
        raise KubernetesTaskExecutionError("kubernetes_manifest_dir_missing")
    root = project_root()
    candidate = Path(raw)
    if not candidate.is_absolute():
        candidate = root / candidate
    candidate = candidate.resolve()
    allowed_roots = [(root / "infra" / "kubernetes").resolve()]
    generated_root = os.getenv("EVM_KUBERNETES_GENERATED_MANIFEST_ROOT", "").strip()
    if generated_root:
        allowed_roots.append(Path(generated_root).resolve())
    if not any(candidate.is_relative_to(allowed_root) for allowed_root in allowed_roots):
        raise KubernetesTaskExecutionError("kubernetes_manifest_dir_not_allowed")
    if not (candidate / "kustomization.yaml").is_file():
        raise KubernetesTaskExecutionError("kustomization_missing")
    return candidate


def resolve_progress_path(value: object) -> Path | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    candidate = Path(raw).resolve()
    lifecycle_root = Path(
        os.getenv(
            "EVM_LIFECYCLE_RUN_ROOT",
            "F:/EnterpriseMLOps_Data/enterprise-vision-mlops/artifacts/w7/lifecycle_runs",
        )
    ).resolve()
    if not candidate.is_relative_to(lifecycle_root):
        raise KubernetesTaskExecutionError("kubernetes_progress_path_not_allowed")
    return candidate


def run_command(
    runner: Runner,
    command: list[str],
    *,
    timeout: int,
) -> subprocess.CompletedProcess[str]:
    return runner(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=False,
    )


def observe_exact_kubernetes_task(
    task_id: str,
    *,
    runner: Runner = subprocess.run,
) -> KubernetesTaskObservation:
    """Observe one previously dispatched Job without creating or replacing it."""

    task = next((item for item in read_tasks().tasks if item.task_id == task_id), None)
    if task is None:
        raise KubernetesTaskExecutionError("task_not_found")
    if task.task_type != "kubernetes_job":
        raise KubernetesTaskExecutionError("task_is_not_kubernetes_job")
    if task.status not in {"running", "done"}:
        raise KubernetesTaskExecutionError(
            f"kubernetes_reconciliation_task_not_observable:{task.status}"
        )
    if task.config_payload.get("adapter") != "host-kubectl-bridge":
        raise KubernetesTaskExecutionError("kubernetes_adapter_not_allowed")

    manifest_dir = resolve_manifest_dir(task.config_payload.get("manifest_dir"))
    namespace = str(task.config_payload.get("namespace") or "").strip()
    job_name = str(task.config_payload.get("job_name") or "").strip()
    lifecycle_run_id = str(task.config_payload.get("lifecycle_run_id") or "").strip()
    if not namespace or not job_name or not lifecycle_run_id:
        raise KubernetesTaskExecutionError("kubernetes_reconciliation_identity_missing")
    runtime_id = f"{namespace}/job/{job_name}"
    if task.runtime_system != "kubernetes" or task.runtime_id != runtime_id:
        raise KubernetesTaskExecutionError("kubernetes_reconciliation_runtime_identity_mismatch")

    expected = expected_job_identity(manifest_dir, namespace=namespace, job_name=job_name)
    command = ["kubectl", "get", "job", job_name, "-n", namespace, "-o", "json"]
    result = run_command(runner, command, timeout=30)
    if result.returncode != 0:
        raise KubernetesTaskExecutionError("kubernetes_reconciliation_job_missing")
    try:
        observed = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise KubernetesTaskExecutionError("kubernetes_reconciliation_job_invalid") from exc
    if not isinstance(observed, dict):
        raise KubernetesTaskExecutionError("kubernetes_reconciliation_job_invalid")

    metadata = observed.get("metadata") if isinstance(observed.get("metadata"), dict) else {}
    observed_name = str(metadata.get("name") or "")
    observed_namespace = str(metadata.get("namespace") or "")
    resource_uid = str(metadata.get("uid") or "")
    labels = metadata.get("labels") if isinstance(metadata.get("labels"), dict) else {}
    if (
        observed_name != job_name
        or observed_namespace != namespace
        or not resource_uid
    ):
        raise KubernetesTaskExecutionError("kubernetes_reconciliation_exact_target_mismatch")
    expected_labels = expected["labels"]
    if any(str(labels.get(key) or "") != value for key, value in expected_labels.items()):
        raise KubernetesTaskExecutionError("kubernetes_reconciliation_label_identity_mismatch")

    observed_containers = job_container_identity(observed)
    if observed_containers != expected["containers"]:
        raise KubernetesTaskExecutionError("kubernetes_reconciliation_workload_identity_mismatch")
    expected_run_label = str(expected_labels.get("evm.openai.local/lifecycle-run") or "")
    if not expected_run_label or short_run_id(lifecycle_run_id) != expected_run_label:
        raise KubernetesTaskExecutionError("kubernetes_reconciliation_run_identity_mismatch")

    conditions = {
        str(item.get("type")): str(item.get("status"))
        for item in (observed.get("status") or {}).get("conditions", [])
        if isinstance(item, dict)
    }
    if conditions.get("Failed") == "True":
        observed_state = "failed"
    elif conditions.get("Complete") == "True":
        observed_state = "complete"
    else:
        observed_state = "running"
    evidence = {
        "schema_version": "evm.lifecycle_kubernetes_reconciliation.v1",
        "task_id": task.task_id,
        "lifecycle_run_id": lifecycle_run_id,
        "runtime_id": runtime_id,
        "resource_uid": resource_uid,
        "observed_state": observed_state,
        "expected_identity": expected,
        "observed_identity": {
            "name": observed_name,
            "namespace": observed_namespace,
            "labels": {key: str(labels.get(key) or "") for key in expected_labels},
            "containers": observed_containers,
        },
        "mutation_performed": False,
    }
    digest = hashlib.sha256(
        json.dumps(evidence, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    evidence_dir = Path(
        os.getenv(
            "EVM_CONTROL_PANEL_LEDGER_ROOT",
            "F:/EnterpriseMLOps_Data/enterprise-vision-mlops/artifacts/w7/operations",
        )
    ) / "task-executions" / task_id
    evidence_dir.mkdir(parents=True, exist_ok=True)
    evidence_path = evidence_dir / f"reconciliation-{digest[:16]}.json"
    write_json(evidence_path, evidence)
    return KubernetesTaskObservation(
        runtime_id=runtime_id,
        resource_uid=resource_uid,
        lifecycle_run_label=expected_run_label,
        candidate_label=str(expected_labels.get("evm.openai.local/candidate-id") or ""),
        observed_state=observed_state,
        evidence_uri=str(evidence_path),
    )


def expected_job_identity(
    manifest_dir: Path,
    *,
    namespace: str,
    job_name: str,
) -> dict[str, object]:
    matches: list[dict[str, object]] = []
    for path in sorted(manifest_dir.glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict) or payload.get("kind") != "Job":
            continue
        metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
        if (
            str(metadata.get("name") or "") == job_name
            and str(metadata.get("namespace") or "") == namespace
        ):
            matches.append(payload)
    if len(matches) != 1:
        raise KubernetesTaskExecutionError(
            f"kubernetes_reconciliation_manifest_match_count:{len(matches)}"
        )
    metadata = matches[0]["metadata"]
    labels = metadata.get("labels") if isinstance(metadata.get("labels"), dict) else {}
    required_labels = {
        key: str(labels.get(key) or "")
        for key in (
            "app.kubernetes.io/part-of",
            "evm.openai.local/lifecycle-run",
            "evm.openai.local/candidate-id",
        )
    }
    if any(not value for value in required_labels.values()):
        raise KubernetesTaskExecutionError("kubernetes_reconciliation_manifest_labels_missing")
    return {
        "name": job_name,
        "namespace": namespace,
        "labels": required_labels,
        "containers": job_container_identity(matches[0]),
    }


def job_container_identity(payload: dict[str, object]) -> list[dict[str, object]]:
    spec = payload.get("spec") if isinstance(payload.get("spec"), dict) else {}
    template = spec.get("template") if isinstance(spec.get("template"), dict) else {}
    pod_spec = template.get("spec") if isinstance(template.get("spec"), dict) else {}
    containers = pod_spec.get("containers") if isinstance(pod_spec.get("containers"), list) else []
    identities: list[dict[str, object]] = []
    for item in containers:
        if not isinstance(item, dict):
            continue
        environment = item.get("env") if isinstance(item.get("env"), list) else []
        revision_values = {
            str(entry.get("name")): str(entry.get("value") or "")
            for entry in environment
            if isinstance(entry, dict)
            and entry.get("name")
            in {
                "EVM_EXPECTED_COMPONENT_SOURCE_REVISION",
                "EVM_IMAGE_SOURCE_REVISION",
                "EVM_LIFECYCLE_RUN_ID",
            }
        }
        identities.append(
            {
                "name": str(item.get("name") or ""),
                "image": str(item.get("image") or ""),
                "revision_env": revision_values,
            }
        )
    identities.sort(key=lambda item: str(item["name"]))
    if not identities or any(not item["name"] or not item["image"] for item in identities):
        raise KubernetesTaskExecutionError("kubernetes_reconciliation_container_identity_missing")
    return identities


def wait_for_job(
    runner: Runner,
    *,
    namespace: str,
    job_name: str,
    timeout_seconds: int,
    command_log: list[dict[str, object]],
    progress_path: Path | None = None,
    progress_callback: ProgressCallback | None = None,
    cancel_requested: Callable[[], bool] | None = None,
) -> None:
    deadline = time.monotonic() + timeout_seconds
    command = ["kubectl", "get", "job", job_name, "-n", namespace, "-o", "json"]
    previous_progress = ""
    while True:
        if cancel_requested is not None and cancel_requested():
            raise KubernetesTaskCancellationRequested("kubernetes_task_cancelled")
        result = run_command(runner, command, timeout=30)
        command_log.append(result_payload(result, command))
        if result.returncode != 0:
            raise KubernetesTaskExecutionError("kubernetes_job_status_failed")
        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise KubernetesTaskExecutionError("kubernetes_job_status_invalid") from exc
        conditions = {
            str(item.get("type")): str(item.get("status"))
            for item in payload.get("status", {}).get("conditions", [])
            if isinstance(item, dict)
        }
        if progress_path is not None and progress_callback is not None:
            try:
                serialized_progress = progress_path.read_text(encoding="utf-8")
                if serialized_progress != previous_progress:
                    progress = json.loads(serialized_progress)
                    if isinstance(progress, dict):
                        progress_callback(progress)
                        previous_progress = serialized_progress
            except (OSError, json.JSONDecodeError):
                pass
        if conditions.get("Complete") == "True":
            return
        if conditions.get("Failed") == "True":
            raise KubernetesTaskExecutionError("kubernetes_job_failed")
        if time.monotonic() >= deadline:
            raise KubernetesTaskExecutionError("kubernetes_job_timed_out")
        time.sleep(5)


def execute_kubernetes_task(
    task_id: str,
    *,
    runner: Runner = subprocess.run,
    progress_callback: ProgressCallback | None = None,
) -> TaskAssignment:
    task = next((item for item in read_tasks().tasks if item.task_id == task_id), None)
    if task is None:
        raise KubernetesTaskExecutionError("task_not_found")
    if task.task_type != "kubernetes_job":
        raise KubernetesTaskExecutionError("task_is_not_kubernetes_job")
    if task.status not in {"queued", "running"}:
        raise KubernetesTaskExecutionError(f"task_not_executable:{task.status}")
    if task.config_payload.get("adapter") != "host-kubectl-bridge":
        raise KubernetesTaskExecutionError("kubernetes_adapter_not_allowed")

    manifest_dir = resolve_manifest_dir(task.config_payload.get("manifest_dir"))
    namespace = str(task.config_payload.get("namespace") or "").strip()
    job_name = str(task.config_payload.get("job_name") or "").strip()
    timeout_seconds = int(task.config_payload.get("timeout_seconds") or 3600)
    progress_path = resolve_progress_path(task.config_payload.get("progress_path"))
    lifecycle_run_id = str(task.config_payload.get("lifecycle_run_id") or "").strip()
    if not namespace or not job_name:
        raise KubernetesTaskExecutionError("kubernetes_target_missing")

    evidence_dir = Path(
        os.getenv(
            "EVM_CONTROL_PANEL_LEDGER_ROOT",
            "F:/EnterpriseMLOps_Data/enterprise-vision-mlops/artifacts/w7/operations",
        )
    ) / "task-executions" / task_id
    evidence_dir.mkdir(parents=True, exist_ok=True)
    runtime_id = f"{namespace}/job/{job_name}"
    runtime_url = f"kubernetes://docker-desktop/{runtime_id}"
    evidence_uri = str(evidence_dir)
    current = update_task_runtime(
        task_id,
        actor="host-kubectl-bridge",
        event="kubernetes_task_claimed",
        status="running",
        runtime_system="kubernetes",
        runtime_id=runtime_id,
        runtime_url=runtime_url,
        runtime_state="applying",
        runtime_evidence_uri=evidence_uri,
    )
    if current is None:
        raise KubernetesTaskExecutionError("task_not_found_after_claim")

    command_log: list[dict[str, object]] = []
    try:
        context = run_command(runner, ["kubectl", "config", "current-context"], timeout=30)
        command_log.append(result_payload(context, ["kubectl", "config", "current-context"]))
        if context.returncode != 0 or context.stdout.strip() != "docker-desktop":
            raise KubernetesTaskExecutionError("unexpected_kubernetes_context")
        recovering = task.status == "running"
        if not recovering and bool(task.config_payload.get("delete_existing", True)):
            delete = run_command(
                runner,
                ["kubectl", "delete", "job", job_name, "-n", namespace, "--ignore-not-found=true"],
                timeout=120,
            )
            command_log.append(result_payload(delete, ["kubectl", "delete", "job", job_name]))
            if delete.returncode != 0:
                raise KubernetesTaskExecutionError("kubernetes_job_delete_failed")
        if not recovering:
            apply = run_command(runner, ["kubectl", "apply", "-k", str(manifest_dir)], timeout=180)
            command_log.append(result_payload(apply, ["kubectl", "apply", "-k", str(manifest_dir)]))
            if apply.returncode != 0:
                raise KubernetesTaskExecutionError("kubernetes_apply_failed")
        wait_for_job(
            runner,
            namespace=namespace,
            job_name=job_name,
            timeout_seconds=timeout_seconds,
            command_log=command_log,
            progress_path=progress_path,
            progress_callback=progress_callback,
            cancel_requested=(
                lambda: lifecycle_is_cancelled(lifecycle_run_id)
                if lifecycle_run_id
                else False
            ),
        )
        logs = run_command(
            runner,
            ["kubectl", "logs", f"job/{job_name}", "-n", namespace, "--all-containers=true"],
            timeout=120,
        )
        command_log.append(
            result_payload(
                logs,
                ["kubectl", "logs", f"job/{job_name}", "-n", namespace],
            )
        )
        (evidence_dir / "job.log").write_text(logs.stdout + logs.stderr, encoding="utf-8")
        write_json(evidence_dir / "execution.json", {"status": "done", "commands": command_log})
        completed = update_task_runtime(
            task_id,
            actor="host-kubectl-bridge",
            event="kubernetes_task_completed",
            status="done",
            runtime_state="complete",
            runtime_evidence_uri=evidence_uri,
        )
        if completed is None:
            raise KubernetesTaskExecutionError("task_not_found_after_completion")
        return completed
    except KubernetesTaskCancellationRequested:
        cleanup_errors = cancel_kubernetes_resources(
            runner,
            namespace=namespace,
            job_name=job_name,
            manifest_dir=manifest_dir,
            command_log=command_log,
        )
        write_json(
            evidence_dir / "execution.json",
            {
                "status": "cancelled",
                "cleanup_errors": cleanup_errors,
                "commands": command_log,
            },
        )
        cancelled = update_task_runtime(
            task_id,
            actor="host-kubectl-bridge",
            event="kubernetes_task_cancelled",
            status="cancelled",
            runtime_state=("cancelled" if not cleanup_errors else "cancellation_cleanup_failed"),
            runtime_evidence_uri=evidence_uri,
            failure_reason=";".join(cleanup_errors) or None,
        )
        if cancelled is None:
            raise KubernetesTaskExecutionError("task_not_found_after_cancellation")
        return cancelled
    except Exception as exc:
        diagnostic_reason = collect_failure_diagnostics(
            runner,
            namespace=namespace,
            job_name=job_name,
            command_log=command_log,
            evidence_dir=evidence_dir,
        )
        try:
            logs = run_command(
                runner,
                ["kubectl", "logs", f"job/{job_name}", "-n", namespace, "--all-containers=true"],
                timeout=120,
            )
            command_log.append(
                result_payload(
                    logs,
                    ["kubectl", "logs", f"job/{job_name}", "-n", namespace],
                )
            )
            (evidence_dir / "job.log").write_text(
                logs.stdout + logs.stderr,
                encoding="utf-8",
            )
        except Exception as log_exc:
            command_log.append({"command": ["kubectl", "logs"], "error": str(log_exc)})
        failure_reason = str(exc)
        if diagnostic_reason:
            failure_reason = f"{failure_reason}:{diagnostic_reason}"
        write_json(
            evidence_dir / "execution.json",
            {
                "status": "failed",
                "error": str(exc),
                "diagnostic_reason": diagnostic_reason,
                "commands": command_log,
            },
        )
        failed = update_task_runtime(
            task_id,
            actor="host-kubectl-bridge",
            event="kubernetes_task_failed",
            status="failed",
            runtime_state="failed",
            runtime_evidence_uri=evidence_uri,
            failure_reason=failure_reason,
        )
        if failed is None:
            raise KubernetesTaskExecutionError("task_not_found_after_failure") from exc
        return failed


def lifecycle_is_cancelled(run_id: str) -> bool:
    run = get_lifecycle_run(run_id)
    return run is not None and run.state == "cancelled"


def cancel_kubernetes_resources(
    runner: Runner,
    *,
    namespace: str,
    job_name: str,
    manifest_dir: Path,
    command_log: list[dict[str, object]],
) -> list[str]:
    commands = [
        [
            "kubectl",
            "delete",
            "job",
            job_name,
            "-n",
            namespace,
            "--ignore-not-found=true",
            "--wait=true",
            "--timeout=120s",
        ]
    ]
    for filename, kind, namespaced in (
        ("storage-pvc.json", "pvc", True),
        ("storage-pv.json", "pv", False),
    ):
        path = manifest_dir / filename
        if not path.is_file():
            continue
        try:
            resource = json.loads(path.read_text(encoding="utf-8"))
            name = str(resource.get("metadata", {}).get("name") or "")
        except (OSError, AttributeError, json.JSONDecodeError):
            name = ""
        if not re.fullmatch(r"[a-z0-9](?:[-a-z0-9.]{0,251}[a-z0-9])?", name):
            continue
        command = ["kubectl", "delete", kind, name]
        if namespaced:
            command.extend(["-n", namespace])
        command.extend(["--ignore-not-found=true", "--wait=true", "--timeout=120s"])
        commands.append(command)
    errors: list[str] = []
    for command in commands:
        result = run_command(runner, command, timeout=150)
        command_log.append(result_payload(result, command))
        if result.returncode != 0:
            errors.append(f"kubernetes_cancel_cleanup_failed:{command[2]}:{command[3]}")
    return errors


def collect_failure_diagnostics(
    runner: Runner,
    *,
    namespace: str,
    job_name: str,
    command_log: list[dict[str, object]],
    evidence_dir: Path,
) -> str | None:
    selector = f"job-name={job_name}"
    commands = [
        ["kubectl", "describe", "job", job_name, "-n", namespace],
        ["kubectl", "get", "pods", "-n", namespace, "-l", selector, "-o", "json"],
        ["kubectl", "describe", "pods", "-n", namespace, "-l", selector],
    ]
    outputs: list[str] = []
    pod_payload: dict[str, object] = {}
    for command in commands:
        try:
            result = run_command(runner, command, timeout=120)
            command_log.append(result_payload(result, command))
            outputs.append(f"$ {' '.join(command)}\n{result.stdout}{result.stderr}")
            if command[1:3] == ["get", "pods"] and result.returncode == 0:
                try:
                    candidate = json.loads(result.stdout)
                    if isinstance(candidate, dict):
                        pod_payload = candidate
                except json.JSONDecodeError:
                    pass
        except Exception as diagnostic_exc:
            command_log.append({"command": command, "error": str(diagnostic_exc)})
            outputs.append(f"$ {' '.join(command)}\n{diagnostic_exc}")
    (evidence_dir / "diagnostics.log").write_text(
        "\n\n".join(outputs),
        encoding="utf-8",
    )
    return pod_failure_reason(pod_payload)


def pod_failure_reason(payload: dict[str, object]) -> str | None:
    items = payload.get("items")
    if not isinstance(items, list):
        return None
    for item in items:
        if not isinstance(item, dict):
            continue
        status = item.get("status")
        if not isinstance(status, dict):
            continue
        for key in ("initContainerStatuses", "containerStatuses"):
            statuses = status.get(key)
            if not isinstance(statuses, list):
                continue
            for container_status in statuses:
                if not isinstance(container_status, dict):
                    continue
                state = container_status.get("state")
                if not isinstance(state, dict):
                    continue
                for phase in ("waiting", "terminated"):
                    detail = state.get(phase)
                    if isinstance(detail, dict) and detail.get("reason"):
                        return str(detail["reason"])
        conditions = status.get("conditions")
        if isinstance(conditions, list):
            for condition in conditions:
                if (
                    isinstance(condition, dict)
                    and condition.get("status") == "False"
                    and condition.get("reason")
                ):
                    return str(condition["reason"])
    return None


def result_payload(
    result: subprocess.CompletedProcess[str],
    command: list[str],
) -> dict[str, object]:
    return {
        "command": command,
        "exit_code": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr,
    }


def write_json(path: Path, payload: dict[str, object]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Execute one queued Control Panel Kubernetes task.")
    parser.add_argument("--task-id", required=True)
    args = parser.parse_args()
    result = execute_kubernetes_task(args.task_id)
    print(json.dumps(result.model_dump(mode="json"), ensure_ascii=False, indent=2))
    return 0 if result.status == "done" else 2


if __name__ == "__main__":
    raise SystemExit(main())
