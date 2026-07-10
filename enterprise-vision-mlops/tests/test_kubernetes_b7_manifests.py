from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


ROOT = Path("infra/kubernetes/model-runtime")


def _read(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8")


def test_kustomize_renders_b7_training_and_serving_resources() -> None:
    assert shutil.which("kubectl"), "kubectl is required by the W7 execution contract"
    result = subprocess.run(
        ["kubectl", "kustomize", str(ROOT)],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )

    assert "name: evm-training" in result.stdout
    assert "name: evm-staging" in result.stdout
    assert "name: evm-b7-training" in result.stdout
    assert "name: evm-b7-serving" in result.stdout


def test_b7_workloads_fail_closed_without_kubernetes_gpu() -> None:
    training = _read("b7-training-job.yaml")
    serving = _read("b7-serving-deployment.yaml")

    assert training.count('nvidia.com/gpu: "1"') == 2
    assert serving.count('nvidia.com/gpu: "1"') == 2
    assert "EVM_EFFICIENTNET_CANDIDATES" in training
    assert "effnet-b7-img600-finetune-adamw" in training
    assert "TORCH_HOME" in training
    assert "mountPath: /dev/shm" in training
    assert "medium: Memory" in training
    assert "sizeLimit: 2Gi" in training
    assert "EVM_REQUIRE_CUDA" in serving
    assert "EVM_DATASET_VERSION" in serving
    assert 'value: "true"' in serving
    assert "replicas: 0" in serving
    assert "type: Recreate" in serving
    assert "enterprise-vision-mlops-efficientnet-training@sha256:" in training
    assert "enterprise-vision-mlops-efficientnet-serving@sha256:" in serving
    assert ":local" not in training
    assert ":local" not in serving


def test_b7_serving_has_identity_probes_and_read_only_data_mount() -> None:
    serving = _read("b7-serving-deployment.yaml")

    assert "EVM_MODEL_SHA256" in serving
    assert "startupProbe:" in serving
    assert "readinessProbe:" in serving
    assert "livenessProbe:" in serving
    assert "path: /ready" in serving
    assert "path: /health" in serving
    assert "readOnly: true" in serving


def test_f_drive_is_the_only_large_artifact_storage_root() -> None:
    storage = _read("storage.yaml")

    expected = "/run/desktop/mnt/host/f/EnterpriseMLOps_Data/enterprise-vision-mlops"
    assert storage.count(expected) == 2
    assert "namespace: evm-training" in storage
    assert "namespace: evm-staging" in storage
    assert storage.count("claimRef:") == 2
    assert "persistentVolumeReclaimPolicy: Retain" in storage
    assert "storage: 1Ti" in storage
