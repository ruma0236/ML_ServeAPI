from __future__ import annotations

import subprocess
import tomllib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
K8S_ROOT = ROOT / "infra" / "kubernetes" / "expedited-production-validation"


def render(name: str) -> str:
    result = subprocess.run(
        ["kubectl", "kustomize", str(K8S_ROOT / name)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout


def test_expedited_training_is_real_gpu_job_with_immutable_image() -> None:
    manifest = render("training")

    assert "name: evm-b0-expedited-training" in manifest
    assert "namespace: evm-training" in manifest
    assert "nvidia.com/gpu: \"1\"" in manifest
    assert "enterprise-vision-mlops-efficientnet-training@sha256:" in manifest
    assert "w7_b0_expedited_production.toml" in manifest
    assert "/usr/lib/wsl/drivers" in manifest
    assert "libcuda.so.1.1" in manifest
    assert "mock" not in manifest.lower()


def test_expedited_production_has_probes_gpu_and_pinned_model() -> None:
    manifest = render("production")

    assert "name: evm-b0-production" in manifest
    assert "namespace: evm-production" in manifest
    assert "nvidia.com/gpu: \"1\"" in manifest
    assert "enterprise-vision-mlops-efficientnet-serving@sha256:" in manifest
    assert "readinessProbe:" in manifest
    assert "livenessProbe:" in manifest
    assert "startupProbe:" in manifest
    assert "type: NodePort" in manifest
    assert "nodePort: 30800" in manifest
    assert "abcb8504a36c1128d32021722cfedce6357fd73598a52f6c2a0d60aca9d9a27f" in manifest
    assert "__MODEL_SHA256_PENDING__" not in manifest


def test_expedited_policy_is_temporary_and_preserves_standard_thresholds() -> None:
    with (ROOT / "configs" / "w7_b0_expedited_production.toml").open("rb") as fp:
        config = tomllib.load(fp)

    assert config["validation_profile"]["temporary"] is True
    assert config["model_matrix"]["mock_allowed"] is False
    assert config["model_matrix"]["smoke_allowed"] is False
    assert config["candidates"][0]["early_stop_accuracy"] == 0.93
    assert config["candidates"][0]["early_stop_min_epochs"] == 2
    assert config["temporary_override"]["standard_promotion_min_f1"] == 0.75
    assert config["temporary_override"]["override_promotion_min_f1"] == 0.20
