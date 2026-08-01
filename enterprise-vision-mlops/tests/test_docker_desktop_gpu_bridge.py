from __future__ import annotations

from pathlib import Path

TEMPLATE = Path(
    "infra/kubernetes/docker-desktop-gpu/nvidia-device-plugin.yaml.tmpl"
)
PROBE_TEMPLATE = Path(
    "infra/kubernetes/docker-desktop-gpu/gpu-resource-probe.yaml.tmpl"
)
WORKLOAD_PATCH = Path(
    "infra/kubernetes/docker-desktop-gpu/model-runtime-workload-patch.yaml.tmpl"
)
SCRIPT = Path("scripts/dev/configure_docker_desktop_kubernetes_gpu.ps1")


def test_docker_desktop_gpu_plugin_template_is_pinned_and_scoped() -> None:
    manifest = TEMPLATE.read_text(encoding="utf-8")

    assert "namespace: kube-system" in manifest
    assert "image: nvcr.io/nvidia/k8s-device-plugin:v0.18.0" in manifest
    assert "privileged: true" in manifest
    assert "name: PASS_DEVICE_SPECS" in manifest
    assert "value: \"true\"" in manifest
    assert "path: /dev/dxg" in manifest
    assert "path: __WSL_DRIVER_PATH__" in manifest
    assert "mountPath: __WSL_DRIVER_PATH__" in manifest


def test_gpu_workloads_stay_non_privileged_and_request_accounted_gpu() -> None:
    probe = PROBE_TEMPLATE.read_text(encoding="utf-8")
    workload_patch = WORKLOAD_PATCH.read_text(encoding="utf-8")

    assert "nvidia.com/gpu: \"1\"" in probe
    assert "allowPrivilegeEscalation: false" in probe
    assert "privileged: true" not in probe
    assert "privileged: true" not in workload_patch
    assert probe.count("__WSL_DRIVER_PATH__") == 4
    assert workload_patch.count("__WSL_DRIVER_PATH__") == 8
    assert "/usr/lib/wsl/drivers/nvidia-current" not in probe
    assert "/usr/lib/wsl/drivers/nvidia-current" not in workload_patch


def test_gpu_bridge_script_fails_closed_and_records_f_drive_evidence() -> None:
    script = SCRIPT.read_text(encoding="utf-8")

    assert "default-runtime" in script
    assert "nvidia.com/gpu" in script
    assert "Get-WslNvidiaDriverPath" in script
    assert "$currentDriverPath -ne $driverPath" in script
    assert "$allocatableGpuCount -ge 1" in script
    assert "positive nvidia.com/gpu allocatable capacity" in script
    assert "SkipGpuProbe" in script
    assert 'schema_version = "evm.w7.docker_desktop_gpu_bridge.v2"' in script
    assert "throw \"NVIDIA device plugin rollout failed.\"" in script
    assert "F:\\EnterpriseMLOps_Data" in script
