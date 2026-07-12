from __future__ import annotations

import json
import subprocess
from types import SimpleNamespace

import pytest

from evm.control_panel.lifecycle_gpu_handoff import (
    GpuHandoffError,
    acquire_gpu_handoff,
    acquire_training_gpu_handoff,
    release_gpu_handoff,
    release_training_gpu_handoff,
)
from evm.control_panel.lifecycle_kubernetes import ServingBundle, TrainingBundle


class FakeKubectl:
    def __init__(self, *, fail_delete_wait: bool = False):
        self.production_replicas = 1
        self.commands: list[list[str]] = []
        self.fail_delete_wait = fail_delete_wait

    def __call__(self, command: list[str], **_kwargs):
        self.commands.append(command)
        if command == ["kubectl", "get", "nodes", "-o", "json"]:
            return completed(command, stdout=json.dumps({"items": [{"status": {"allocatable": {"nvidia.com/gpu": "1"}}}]}))
        if "get" in command and "deployment/evm-b0-production" in command:
            return completed(
                command,
                stdout=json.dumps(
                    {
                        "spec": {
                            "replicas": self.production_replicas,
                            "selector": {"matchLabels": {"app.kubernetes.io/name": "evm-b0-production"}},
                        }
                    }
                ),
            )
        if "scale" in command and "deployment/evm-b0-production" in command:
            replica_arg = next(item for item in command if item.startswith("--replicas="))
            self.production_replicas = int(replica_arg.split("=", 1)[1])
            return completed(command)
        if (
            self.fail_delete_wait
            and "wait" in command
            and "app.kubernetes.io/name=evm-b0-production" in command
        ):
            self.fail_delete_wait = False
            return completed(command, returncode=1, stderr="timed out waiting for pod deletion")
        return completed(command)


def completed(
    command: list[str],
    *,
    returncode: int = 0,
    stdout: str = "",
    stderr: str = "",
) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(command, returncode, stdout, stderr)


def test_single_gpu_handoff_scales_product_down_and_restores_it(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("EVM_LIFECYCLE_SINGLE_GPU_HANDOFF_ENABLED", "true")
    monkeypatch.setenv("EVM_LIFECYCLE_GPU_HOLDERS", "evm-production/evm-b0-production")
    run = SimpleNamespace(run_id="lifecycle-handoff", artifact_root=str(tmp_path))
    serving = ServingBundle(
        manifest_dir=tmp_path,
        namespace="evm-staging",
        deployment_name="evm-b0-staging",
        endpoint="http://127.0.0.1:30813",
        image="serving@sha256:" + "a" * 64,
    )
    runner = FakeKubectl()

    path = acquire_gpu_handoff(run, serving, runner=runner)

    assert path is not None
    assert runner.production_replicas == 0
    acquired = json.loads(path.read_text(encoding="utf-8"))
    assert acquired["state"] == "acquired"
    assert acquired["holders"][0]["original_replicas"] == 1

    release_gpu_handoff(
        run,
        serving,
        runner=runner,
        reason="staging_validation_completed",
    )

    released = json.loads(path.read_text(encoding="utf-8"))
    assert released["state"] == "released"
    assert released["release_reason"] == "staging_validation_completed"
    assert runner.production_replicas == 1
    assert any("deployment/evm-b0-staging" in command for command in released["commands"])


def test_failed_handoff_acquisition_restores_product(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("EVM_LIFECYCLE_SINGLE_GPU_HANDOFF_ENABLED", "true")
    run = SimpleNamespace(run_id="lifecycle-handoff-failed", artifact_root=str(tmp_path))
    serving = ServingBundle(
        manifest_dir=tmp_path,
        namespace="evm-staging",
        deployment_name="evm-b0-staging",
        endpoint="http://127.0.0.1:30813",
        image="serving@sha256:" + "b" * 64,
    )
    runner = FakeKubectl(fail_delete_wait=True)

    with pytest.raises(GpuHandoffError, match="timed out waiting for pod deletion"):
        acquire_gpu_handoff(run, serving, runner=runner)

    assert runner.production_replicas == 1
    payload = json.loads((tmp_path / "kubernetes" / "gpu_handoff.json").read_text(encoding="utf-8"))
    assert payload["state"] == "acquire_failed"
    assert payload["blockers"]


def test_training_handoff_releases_product_after_gpu_job(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("EVM_LIFECYCLE_SINGLE_GPU_HANDOFF_ENABLED", "true")
    monkeypatch.setenv("EVM_LIFECYCLE_GPU_HOLDERS", "evm-production/evm-b0-production")
    run = SimpleNamespace(run_id="lifecycle-training-handoff", artifact_root=str(tmp_path))
    training = TrainingBundle(
        manifest_dir=tmp_path,
        namespace="evm-training",
        job_name="evm-lifecycle-train-proof",
        candidate_id="efficientnet-b0",
        image="training@sha256:" + "c" * 64,
    )
    runner = FakeKubectl()

    path = acquire_training_gpu_handoff(run, training, runner=runner)

    assert path is not None
    assert path.name == "training_gpu_handoff.json"
    assert runner.production_replicas == 0
    acquired = json.loads(path.read_text(encoding="utf-8"))
    assert acquired["state"] == "acquired"
    assert acquired["target"] == {
        "kind": "Job",
        "name": "evm-lifecycle-train-proof",
        "namespace": "evm-training",
    }

    release_training_gpu_handoff(
        run,
        training,
        runner=runner,
        reason="training_task_done",
    )

    released = json.loads(path.read_text(encoding="utf-8"))
    assert released["state"] == "released"
    assert released["release_reason"] == "training_task_done"
    assert runner.production_replicas == 1
    assert not any("evm-lifecycle-train-proof" in " ".join(command) for command in runner.commands)
