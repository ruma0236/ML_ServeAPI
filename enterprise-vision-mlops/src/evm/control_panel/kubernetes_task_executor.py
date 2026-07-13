from __future__ import annotations

import argparse
import json
import os
import subprocess
import time
from pathlib import Path
from typing import Callable

from evm.control_panel.operations import read_tasks, update_task_runtime
from evm.control_panel.schemas import TaskAssignment


Runner = Callable[..., subprocess.CompletedProcess[str]]


class KubernetesTaskExecutionError(RuntimeError):
    pass


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


def wait_for_job(
    runner: Runner,
    *,
    namespace: str,
    job_name: str,
    timeout_seconds: int,
    command_log: list[dict[str, object]],
) -> None:
    deadline = time.monotonic() + timeout_seconds
    command = ["kubectl", "get", "job", job_name, "-n", namespace, "-o", "json"]
    while True:
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
