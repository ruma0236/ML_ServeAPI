# 2026-07-10 W7 EfficientNet Runtime Preflight

## Scope

This is an `EVM-237` preflight checkpoint. It prepares and verifies the local
Torch/CUDA runtime required for real EfficientNet-B0/B7 training. It does not
complete `EVM-237`; no EfficientNet candidate has been trained yet.

## Runtime Location

Primary W7 Torch runtime:

```text
F:/evm_w7_torch
```

Python executable:

```text
F:/evm_w7_torch/python.exe
```

The first attempted Conda env under the long path
`F:/EnterpriseMLOps_Data/enterprise-vision-mlops/runtimes/conda/evm_w7_torch`
was removed because PyTorch installation failed with Windows `WinError 206`
path-length errors inside the PyTorch wheel metadata. The short F-drive path
resolved that issue.

## Install Commands

```powershell
C:\Users\opop0\miniconda3\Library\bin\conda.bat create -p F:\evm_w7_torch python=3.11 pip -y
F:\evm_w7_torch\python.exe -m pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu126
F:\evm_w7_torch\python.exe -m pip install -e . mlflow==2.18.0 scikit-learn==1.5.2 pandas==2.2.3 matplotlib==3.9.2 pytest==8.3.4 pyarrow==18.1.0
```

The CUDA wheel index follows the official PyTorch pip install pattern for
CUDA-specific builds.

## Verification

CUDA tensor probe:

```powershell
F:\evm_w7_torch\python.exe -c "import torch; x = torch.randn(1024, 1024, device='cuda'); y = x @ x.T; torch.cuda.synchronize(); print(torch.__version__, torch.cuda.get_device_name(0), tuple(y.shape))"
```

Observed runtime:

```json
{
  "python": "3.11.15",
  "torch": "2.13.0+cu126",
  "torchvision": "0.28.0+cu126",
  "cuda_available": true,
  "cuda_device_count": 1,
  "cuda_version": "12.6",
  "cuda_device_name": "NVIDIA GeForce RTX 4080 SUPER",
  "cuda_memory_total": 17170956288,
  "pyarrow": "18.1.0",
  "mlflow": "2.18.0",
  "sklearn": "1.5.2"
}
```

Project verification:

```powershell
F:\evm_w7_torch\python.exe -m pytest tests\test_w7_real_test_policy.py tests\test_control_panel_aggregation.py -q
```

Result:

```text
7 passed in 0.20s
```

MLflow server health:

```powershell
Invoke-RestMethod -Uri http://localhost:5000/health
```

Result:

```text
OK
```

## Inventory Script Update

`scripts/dev/w7_gpu_vlm_serving_inventory.ps1` now accepts a `-TorchPython`
argument or `EVM_W7_TORCH_PYTHON` environment variable so the W7 Torch runtime
can be used for CUDA checks:

```powershell
scripts\dev\w7_gpu_vlm_serving_inventory.ps1 -TorchPython F:\evm_w7_torch\python.exe -SkipRemoteJob
```

Latest inventory evidence:

```text
F:/EnterpriseMLOps_Data/enterprise-vision-mlops/artifacts/w7/gpu_vlm_serving/evm-gpu-vlm-serving-20260710T115218
```

## Current Result

`EVM-237` can proceed to implementation of
`src/evm/pipelines/efficientnet_training/run.py` and
`src/evm/core/torch_efficientnet.py`.

Remaining `EVM-237` evidence still required:

- EfficientNet-B0 candidate runs with at least 5 epochs.
- EfficientNet-B7 candidate runs with at least 3 epochs.
- MLflow run ids per candidate.
- model artifacts under
  `F:/EnterpriseMLOps_Data/enterprise-vision-mlops/artifacts/w7/efficientnet`.
- split manifest snapshot.
- training history.
- confusion matrices.
- GPU profile per candidate.
- Control Panel `CycleRun.model_matrix` with actual candidate evidence.
