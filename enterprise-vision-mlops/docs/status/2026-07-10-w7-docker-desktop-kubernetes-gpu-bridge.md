# W7 Docker Desktop Kubernetes GPU Bridge

Date: 2026-07-10
Issue: EVM-226

## Decision

The Docker Desktop Kubernetes GPU advertisement blocker is resolved for the
local W7 cluster. The bridge is explicitly scoped to Docker Desktop on Windows
with the WSL2 GPU-PV backend. It is not the production Linux GPU deployment
path, where NVIDIA Container Toolkit and the official device plugin or GPU
Operator remain the expected implementation.

## Root Cause

- Docker containers could use the RTX 4080 SUPER through `--gpus all`.
- Docker Desktop Kubernetes used cri-dockerd, which did not expose an `nvidia`
  RuntimeClass handler and did not translate CDI annotations or CRI CDI device
  requests into Docker GPU injection.
- The Kubernetes node therefore did not advertise `nvidia.com/gpu`, even
  though the WSL2 host exposed `/dev/dxg` and the current driver libraries.

## Implementation

- `scripts/dev/configure_docker_desktop_kubernetes_gpu.ps1`:
  - sets Docker Desktop's default Docker runtime to `nvidia` when needed;
  - discovers the active WSL driver directory at runtime;
  - deploys the pinned NVIDIA device plugin `v0.18.0`;
  - mounts only the WSL2 GPU-PV driver surfaces into the plugin;
  - enables `PASS_DEVICE_SPECS=true`, so `/dev/dxg` is granted through the
    device-plugin allocation response;
  - runs a non-privileged Pod that requests `nvidia.com/gpu: 1`.
- `infra/kubernetes/docker-desktop-gpu/` contains the plugin, probe, and B7
  workload patch templates. Training and serving remain non-privileged and
  receive the WSL driver directories read-only.
- `scripts/dev/w7_kubernetes_b7_execution_proof.ps1` now invokes this bridge
  instead of reapplying the unmodified static device-plugin manifest.

## Live Evidence

- Context: `docker-desktop`
- Node: `docker-desktop`
- Docker default runtime: `nvidia`
- Device plugin: `nvcr.io/nvidia/k8s-device-plugin:v0.18.0`
- GPU capacity: `1`
- GPU allocatable: `1`
- Non-privileged resource probe: pass
- Probe output: `NVIDIA GeForce RTX 4080 SUPER, 610.62, 16376 MiB`
- Evidence:
  `F:/EnterpriseMLOps_Data/enterprise-vision-mlops/artifacts/w7/kubernetes_gpu_bridge/evm-gpu-bridge-20260710T135448Z`

## Verification

```powershell
powershell -NoProfile -ExecutionPolicy Bypass `
  -File scripts/dev/configure_docker_desktop_kubernetes_gpu.ps1
kubectl get node docker-desktop `
  -o jsonpath="{.status.allocatable.nvidia\.com/gpu}"
kubectl logs -n evm-training pod/evm-gpu-resource-probe
pytest tests/test_docker_desktop_gpu_bridge.py -q
```

Results:

- GPU bridge script: pass
- `nvidia.com/gpu`: `1`
- GPU resource probe: pass
- bridge contract tests: `3 passed`

## Boundary

EVM-226 remains In Progress at this checkpoint. GPU scheduling is now
available, but the selected B7 training Job, serving rollout, inference,
controlled failure, and rollback evidence must still complete before the issue
can be marked Done.

