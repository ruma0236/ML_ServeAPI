# 2026-07-10 W7 GPU/VLM Serving Deployment Design

## Scope

This closes `EVM-227` as a deployment design and resource-evidence checkpoint.
It does not claim that Kubernetes serving, KServe, Triton, Ray Serve, or vLLM
has been deployed.

## Evidence

Reusable proof command:

```powershell
scripts\dev\w7_gpu_vlm_serving_inventory.ps1
```

Evidence root:

```text
F:/EnterpriseMLOps_Data/enterprise-vision-mlops/artifacts/w7/gpu_vlm_serving
```

Reference full run:

```text
F:/EnterpriseMLOps_Data/enterprise-vision-mlops/artifacts/w7/gpu_vlm_serving/evm-gpu-vlm-serving-20260710T112747
```

Summary artifact:

```text
F:/EnterpriseMLOps_Data/enterprise-vision-mlops/artifacts/w7/gpu_vlm_serving/evm-gpu-vlm-serving-20260710T112747/gpu_vlm_serving_summary.json
```

The proof runner captured:

- `nvidia-smi` GPU inventory.
- Python Torch/CUDA probe.
- Docker Compose service state.
- Docker Desktop Kubernetes status.
- Mac mini remote worker inventory.
- Mac mini remote job execution and artifact collection.

## Current Resource Findings

Windows GPU:

```text
NVIDIA GeForce RTX 4080 SUPER, 16376 MiB total, 5288 MiB used, 10758 MiB free, driver 610.62
```

Default Python runtime:

```json
{
  "python": "3.14.3",
  "platform": "Windows-11-10.0.26200-SP0",
  "torch_error": "ModuleNotFoundError(\"No module named 'torch'\")"
}
```

Mac mini worker:

- `ruma_macmini` is online over Tailscale.
- SSH port is open.
- remote execution is ready.
- remote job status: `success`.
- remote resource report:
  - host: `rumaui-Macmini.local`
  - OS: `Darwin 25.5.0`
  - architecture: `arm64`
  - CPU: `12`
  - memory: `25769803776` bytes
  - Python: `3.9.6`
  - uv: `0.11.21`

Kubernetes:

- Docker Desktop Kubernetes is not running.
- In-cluster serving proof remains blocked until `kubectl config
  current-context` and `kubectl get nodes` succeed.

## Deployment Decision

Near-term W7 execution should use the Windows RTX 4080 SUPER as the primary
CUDA training and GPU serving candidate after installing a pinned Torch runtime.
The Mac mini M4 Pro should remain an ARM64 evaluator, artifact verifier, remote
CI candidate, and optional MPS/CoreML experiment target. It should not be the
primary CUDA trainer.

Serving runtime interpretation:

| Runtime | W7 Role | Current Decision |
|---|---|---|
| FastAPI registry serving | Current local serving path | keep for registry-driven API proof and Control Panel integration |
| Triton | EfficientNet/CV production serving candidate | preferred next runtime for Torch/ONNX EfficientNet after model artifacts exist |
| vLLM | LLM/VLM OpenAI-compatible serving candidate | use only after selected VLM support and VRAM fit are confirmed |
| Ray Serve | Python-native multi-model composition | useful for orchestration experiments, not the first hard serving dependency |
| KServe | Kubernetes-native rollout layer | blocked until Kubernetes proof is available |

## Scheduling Constraints

- EfficientNet-B0 can run first for fast CUDA runtime validation and matrix
  feedback.
- EfficientNet-B7 should be exclusive on the RTX GPU because of image
  resolution, memory pressure, and longer runtime.
- CPU fallback must not be accepted as W7 EfficientNet completion evidence.
- Mac mini jobs can validate ARM64 compatibility and collected artifacts, but
  cannot replace CUDA evidence.
- KServe/Triton-on-Kubernetes must stay blocked until `EVM-226` produces real
  pod/job evidence.

## Blockers For Implementation

The design is complete, but production serving implementation remains blocked
by:

- missing pinned Torch/TorchVision CUDA runtime in the default project Python;
- disabled/stopped Docker Desktop Kubernetes;
- missing real EfficientNet-B0/B7 MLflow artifacts from `EVM-237`;
- missing VLM model selection/license/VRAM compatibility evidence for vLLM.

## Next Handoff

`EVM-237` should install or select a pinned Torch/TorchVision CUDA runtime,
run the EfficientNet-B0/B7 real-test matrix, and emit MLflow artifacts plus a
Control Panel `model_matrix`. After that, Triton/ONNX packaging can become the
first concrete production-serving implementation track.
