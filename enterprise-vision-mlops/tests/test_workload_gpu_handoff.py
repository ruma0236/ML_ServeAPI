from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from evm.control_panel.scenario_workloads import atomic_write_json, payload_sha256
from evm.model_runtime import workload_gpu_handoff as handoff
from evm.model_runtime.common import ModelRuntimeError


def test_failed_scale_down_convergence_restores_exact_holder(tmp_path: Path, monkeypatch) -> None:
    run = SimpleNamespace(
        run_id="run-1",
        actor="requester",
        identity=SimpleNamespace(identity_sha256="a" * 64, source_commit="b" * 40),
    )
    request_path = tmp_path / "gpu-handoff-request.json"
    material = {
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
    atomic_write_json(
        request_path,
        {
            "state": "approved",
            **material,
            "request_digest": payload_sha256(material),
            "approver": "platform-approver",
        },
    )
    monkeypatch.setattr(handoff, "gpu_handoff_request_path", lambda _run: request_path)

    deployment = {
        "metadata": {"uid": "deployment-uid"},
        "spec": {"replicas": 1, "selector": {"matchLabels": {"app": "b0"}}},
        "status": {"availableReplicas": 1},
    }
    pods = {
        "items": [
            {
                "metadata": {"uid": "pod-uid", "name": "pod-1"},
                "status": {"phase": "Running"},
            }
        ]
    }
    monkeypatch.setattr(
        handoff,
        "kubectl_json",
        lambda command: deployment if any("deployment/" in item for item in command) else pods,
    )
    commands: list[list[str]] = []
    monkeypatch.setattr(
        handoff,
        "run_command",
        lambda command: commands.append(command)
        or SimpleNamespace(returncode=0, stdout="", stderr=""),
    )
    calls = 0

    def wait(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise ModelRuntimeError("injected_convergence_timeout")

    monkeypatch.setattr(handoff, "wait_for_pod_count", wait)

    with pytest.raises(ModelRuntimeError, match="scale_down_not_converged"):
        handoff.acquire_workload_gpu_handoff(run, timeout_seconds=1)

    assert commands[0][-1] == "--replicas=0"
    assert commands[1][-1] == "--replicas=1"
    evidence = __import__("json").loads(
        (tmp_path / "gpu-handoff-evidence.json").read_text(encoding="utf-8")
    )
    assert evidence["state"] == "acquire_failed_restored"
