from __future__ import annotations

import json
import subprocess
import sys

import pytest

from fastapi.testclient import TestClient

from evm.scale_validation import s2_airflow_fixture
from evm.scale_validation.s2_airflow_fixture import app


def test_fixture_is_deterministic_and_counts_one_external_effect():
    client = TestClient(app)
    client.post("/reset")
    payload = {
        "dag_run_id": "run-one",
        "conf": {"s2_failure_mode": "transient_once"},
    }

    assert client.post("/api/v1/dags/dag-one/dagRuns", json=payload).status_code == 503
    assert client.post("/api/v1/dags/dag-one/dagRuns", json=payload).status_code == 200
    assert client.post("/api/v1/dags/dag-one/dagRuns", json=payload).status_code == 409
    assert client.get("/api/v1/dags/dag-one/dagRuns/run-one").status_code == 200
    evidence = client.get("/evidence").json()
    assert evidence["attempts"] == 3
    assert evidence["unique_external_effects"] == 1
    assert evidence["duplicate_external_effects"] == 0


def test_fixture_binds_cuda_probe_to_existing_gpu_profile(monkeypatch):
    client = TestClient(app)
    client.post("/reset")
    monkeypatch.setattr(
        s2_airflow_fixture,
        "_execute_cuda_probe",
        lambda task_id, seed: {
            "backend": "cuda",
            "device_count": 1,
            "result_sha256": "a" * 64,
            "peak_allocated_bytes": 4096,
            "nonzero_activity": bool(task_id and seed),
        },
    )
    response = client.post(
        "/api/v1/dags/dag-gpu/dagRuns",
        json={
            "dag_run_id": "run-gpu",
            "conf": {
                "control_panel_task_id": "task-gpu",
                "resource_profile": "windows-rtx-4080-super",
                "s2_cuda_probe": True,
                "s2_cuda_seed": 20260816,
                "s2_terminal_state": "success",
            },
        },
    )
    assert response.status_code == 200
    evidence = client.get("/evidence").json()
    assert evidence["cuda_probe_count"] == 1
    assert evidence["cuda_failure_count"] == 0
    assert evidence["cuda_nonzero_activity_count"] == 1
    assert evidence["cuda_peak_allocated_bytes"] == 4096


def test_cuda_probe_runs_on_the_current_trusted_cuda_device():
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import json\n"
                "try:\n import torch\n"
                "except ModuleNotFoundError:\n raise SystemExit(77)\n"
                "from evm.scale_validation.s2_airflow_fixture import _execute_cuda_probe\n"
                "if not torch.cuda.is_available(): raise SystemExit(77)\n"
                "print(json.dumps(_execute_cuda_probe('task-cuda-contract',20260816)))\n"
            ),
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode == 77:
        pytest.skip("trusted CUDA device is unavailable")
    assert result.returncode == 0, result.stderr

    probe = json.loads(result.stdout.strip().splitlines()[-1])

    assert probe["backend"] == "cuda"
    assert probe["device_count"] >= 1
    assert probe["nonzero_activity"] is True
    assert probe["peak_allocated_bytes"] > 0
    assert len(probe["result_sha256"]) == 64
