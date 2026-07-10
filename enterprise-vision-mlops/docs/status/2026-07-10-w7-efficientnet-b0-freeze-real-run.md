# 2026-07-10 W7 EfficientNet-B0 Freeze Real Run

## Scope

This checkpoint records the first real W7 `EVM-237` EfficientNet candidate run.
It executes `effnet-b0-img224-freeze-adamw` against the real VisA split with
Torch/CUDA and MLflow evidence. It does not close `EVM-237`; only one of four
configured candidates has run.

## Command

```powershell
$env:EVM_EFFICIENTNET_CANDIDATES = "effnet-b0-img224-freeze-adamw"
F:\evm_w7_torch\python.exe scripts\run_pipeline.py efficientnet-training --config configs\w7_efficientnet_real_test.toml
```

## Evidence

- Matrix id: `w7-efficientnet-real-test-matrix`
- Candidate: `effnet-b0-img224-freeze-adamw`
- Dataset version: `visa-open-data-f1f1c9ee9922`
- Split: `train=6504`, `validation=2136`, `test=2181`
- Epochs: `5`
- Optimizer steps: `1020`
- Training duration: `362.219` seconds
- MLflow run id: `eeac494a65b447e4bb4a65ce7a101ca9`
- Artifact root:
  `F:/EnterpriseMLOps_Data/enterprise-vision-mlops/artifacts/w7/efficientnet/w7-efficientnet-real-test-matrix/effnet-b0-img224-freeze-adamw`
- Matrix summary:
  `F:/EnterpriseMLOps_Data/enterprise-vision-mlops/artifacts/w7/efficientnet/latest_model_matrix.json`

## Metrics

- Accuracy: `0.797341`
- Precision: `0.286807`
- Recall: `0.684932`
- F1: `0.404313`
- AUROC: `0.839289`
- Latency p95: `4.532778 ms`
- GPU memory peak: `989.201 MB`

Promotion blockers:

- `accuracy<0.8`
- `f1<0.75`

## Control Panel State

`CycleRun.model_matrix` now reads the evidence from
`latest_model_matrix.json`.

- Matrix status: `warn`
- Configured candidates: `4`
- Executed candidates: `1`
- First candidate status: `pass`
- First candidate run URI:
  `http://localhost:5000/#/runs/eeac494a65b447e4bb4a65ce7a101ca9`

The matrix remains `warn`, not `pass`, because three configured candidates have
not run yet.

## MLflow Logging Fix

The initial fluent MLflow client path failed on Windows because the MLflow
console output attempted to encode an emoji through `cp949`. The training
helpers now use the existing REST MLflow client to create runs and log
parameters/metrics without console encoding side effects.

## Remaining Closure Evidence

`EVM-237` remains open until these candidates also produce evidence:

- `effnet-b0-img224-finetune-sgd`
- `effnet-b7-img600-freeze-adamw`
- `effnet-b7-img600-finetune-adamw`

After all candidates run, `EVM-238-B` must validate the full real-test evidence.
