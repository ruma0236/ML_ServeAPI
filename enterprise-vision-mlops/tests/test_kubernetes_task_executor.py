from __future__ import annotations

import json
import subprocess
from pathlib import Path

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
