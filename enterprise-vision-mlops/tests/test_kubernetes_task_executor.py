from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import evm.control_panel.kubernetes_task_executor as kubernetes_task_executor
from evm.control_panel.kubernetes_task_executor import execute_kubernetes_task
from evm.control_panel.operations import create_task_assignment
from evm.control_panel.schemas import TaskAssignmentRequest


def request() -> TaskAssignmentRequest:
    return TaskAssignmentRequest(
        cycle_id="cycle-expedited",
        task_type="kubernetes_job",
        owner="ml-platform",
        priority="high",
        resource_profile="docker-desktop-gpu",
        approval_policy="auto",
        config_payload={
            "adapter": "host-kubectl-bridge",
            "manifest_dir": "infra/kubernetes/expedited",
            "namespace": "evm-training",
            "job_name": "evm-b0-expedited-training",
            "timeout_seconds": 60,
            "delete_existing": True,
        },
        dry_run=False,
    )


def completed(command: list[str], stdout: str = "", returncode: int = 0):
    return subprocess.CompletedProcess(command, returncode, stdout=stdout, stderr="")


def test_host_bridge_executes_allowlisted_kustomize_job(tmp_path, monkeypatch):
    project = tmp_path / "project"
    manifest_dir = project / "infra" / "kubernetes" / "expedited"
    manifest_dir.mkdir(parents=True)
    (manifest_dir / "kustomization.yaml").write_text("resources: []\n", encoding="utf-8")
    ledger = tmp_path / "ledger"
    monkeypatch.setenv("EVM_PROJECT_ROOT", str(project))
    monkeypatch.setenv("EVM_CONTROL_PANEL_LEDGER_ROOT", str(ledger))
    task = create_task_assignment(request())
    calls: list[list[str]] = []

    def runner(command, **_kwargs):
        calls.append(command)
        if command[1:3] == ["config", "current-context"]:
            return completed(command, "docker-desktop\n")
        if command[1:3] == ["get", "job"]:
            return completed(
                command,
                json.dumps({"status": {"conditions": [{"type": "Complete", "status": "True"}]}}),
            )
        if command[1] == "logs":
            return completed(command, "training complete\n")
        return completed(command, "ok\n")

    result = execute_kubernetes_task(task.task_id, runner=runner)

    assert task.status == "queued"
    assert result.status == "done"
    assert result.runtime_system == "kubernetes"
    assert result.runtime_state == "complete"
    assert result.runtime_evidence_uri
    assert (Path(result.runtime_evidence_uri) / "job.log").read_text(encoding="utf-8") == "training complete\n"
    assert any(command[1:3] == ["apply", "-k"] for command in calls)
    assert any(command[1:3] == ["get", "job"] for command in calls)


def test_host_bridge_streams_allowlisted_training_progress(tmp_path, monkeypatch):
    project = tmp_path / "project"
    manifest_dir = project / "infra" / "kubernetes" / "expedited"
    manifest_dir.mkdir(parents=True)
    (manifest_dir / "kustomization.yaml").write_text("resources: []\n", encoding="utf-8")
    lifecycle_root = tmp_path / "lifecycle-runs"
    progress_path = lifecycle_root / "run-1" / "training-progress.json"
    progress_path.parent.mkdir(parents=True)
    progress_path.write_text(
        json.dumps({"phase": "training", "epoch": 2, "unit_progress": 0.5}),
        encoding="utf-8",
    )
    monkeypatch.setenv("EVM_PROJECT_ROOT", str(project))
    monkeypatch.setenv("EVM_LIFECYCLE_RUN_ROOT", str(lifecycle_root))
    monkeypatch.setenv("EVM_CONTROL_PANEL_LEDGER_ROOT", str(tmp_path / "ledger"))
    task_request = request().model_copy(deep=True)
    task_request.config_payload["progress_path"] = str(progress_path)
    task = create_task_assignment(task_request)
    observed: list[dict[str, object]] = []

    def runner(command, **_kwargs):
        if command[1:3] == ["config", "current-context"]:
            return completed(command, "docker-desktop\n")
        if command[1:3] == ["get", "job"]:
            return completed(
                command,
                json.dumps({"status": {"conditions": [{"type": "Complete", "status": "True"}]}}),
            )
        return completed(command, "ok\n")

    result = execute_kubernetes_task(
        task.task_id,
        runner=runner,
        progress_callback=observed.append,
    )

    assert result.status == "done"
    assert observed == [{"phase": "training", "epoch": 2, "unit_progress": 0.5}]


def test_host_bridge_cancels_job_storage_and_task_when_lifecycle_is_cancelled(
    tmp_path, monkeypatch
):
    project = tmp_path / "project"
    manifest_dir = project / "infra" / "kubernetes" / "expedited"
    manifest_dir.mkdir(parents=True)
    (manifest_dir / "kustomization.yaml").write_text("resources: []\n", encoding="utf-8")
    (manifest_dir / "storage-pvc.json").write_text(
        json.dumps({"metadata": {"name": "training-pvc"}}), encoding="utf-8"
    )
    (manifest_dir / "storage-pv.json").write_text(
        json.dumps({"metadata": {"name": "training-pv"}}), encoding="utf-8"
    )
    monkeypatch.setenv("EVM_PROJECT_ROOT", str(project))
    monkeypatch.setenv("EVM_CONTROL_PANEL_LEDGER_ROOT", str(tmp_path / "ledger"))
    monkeypatch.setattr(
        kubernetes_task_executor,
        "get_lifecycle_run",
        lambda _run_id: SimpleNamespace(state="cancelled"),
    )
    task_request = request().model_copy(deep=True)
    task_request.config_payload["lifecycle_run_id"] = "lifecycle-cancelled-1"
    task = create_task_assignment(task_request)
    calls: list[list[str]] = []

    def runner(command, **_kwargs):
        calls.append(command)
        if command[1:3] == ["config", "current-context"]:
            return completed(command, "docker-desktop\n")
        return completed(command, "ok\n")

    result = execute_kubernetes_task(task.task_id, runner=runner)

    assert result.status == "cancelled"
    assert result.runtime_state == "cancelled"
    assert any(command[1:4] == ["delete", "job", "evm-b0-expedited-training"] for command in calls)
    assert any(command[1:4] == ["delete", "pvc", "training-pvc"] for command in calls)
    assert any(command[1:4] == ["delete", "pv", "training-pv"] for command in calls)
    execution = json.loads(
        (Path(result.runtime_evidence_uri or "") / "execution.json").read_text(encoding="utf-8")
    )
    assert execution["status"] == "cancelled"
    assert execution["cleanup_errors"] == []


def test_host_bridge_records_failed_job(tmp_path, monkeypatch):
    project = tmp_path / "project"
    manifest_dir = project / "infra" / "kubernetes" / "expedited"
    manifest_dir.mkdir(parents=True)
    (manifest_dir / "kustomization.yaml").write_text("resources: []\n", encoding="utf-8")
    monkeypatch.setenv("EVM_PROJECT_ROOT", str(project))
    monkeypatch.setenv("EVM_CONTROL_PANEL_LEDGER_ROOT", str(tmp_path / "ledger"))
    task = create_task_assignment(request())

    def runner(command, **_kwargs):
        if command[1:3] == ["config", "current-context"]:
            return completed(command, "docker-desktop\n")
        if command[1:3] == ["get", "job"]:
            return completed(
                command,
                json.dumps({"status": {"conditions": [{"type": "Failed", "status": "True"}]}}),
            )
        if command[1:3] == ["get", "pods"]:
            return completed(
                command,
                json.dumps(
                    {
                        "items": [
                            {
                                "status": {
                                    "containerStatuses": [
                                        {
                                            "state": {
                                                "waiting": {
                                                    "reason": "CreateContainerError",
                                                    "message": "read-only mount conflict",
                                                }
                                            }
                                        }
                                    ]
                                }
                            }
                        ]
                    }
                ),
            )
        if command[1] == "describe":
            return completed(command, "mount setup failed\n")
        if command[1] == "logs":
            return completed(command, "training failed\n")
        return completed(command, "ok\n")

    result = execute_kubernetes_task(task.task_id, runner=runner)

    assert result.status == "failed"
    assert result.runtime_state == "failed"
    assert result.failure_reason == "kubernetes_job_failed:CreateContainerError"
    evidence = Path(result.runtime_evidence_uri or "")
    assert "mount setup failed" in (evidence / "diagnostics.log").read_text(encoding="utf-8")
    execution = json.loads((evidence / "execution.json").read_text(encoding="utf-8"))
    assert execution["diagnostic_reason"] == "CreateContainerError"
