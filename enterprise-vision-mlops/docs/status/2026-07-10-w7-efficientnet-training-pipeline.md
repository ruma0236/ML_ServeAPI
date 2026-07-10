# 2026-07-10 W7 EfficientNet Training Pipeline Checkpoint

## Scope

This checkpoint advances `EVM-237` from runtime preflight into implementation.
It adds the W7 `efficientnet-training` pipeline, Control Panel model-matrix
evidence ingestion, and tests for fail-closed split validation. It does not
close `EVM-237`; real EfficientNet-B0/B7 training has not been executed yet.

## Implementation

- `scripts/run_pipeline.py` now exposes `efficientnet-training`.
- `src/evm/pipelines/efficientnet_training/run.py` reads
  `configs/w7_efficientnet_real_test.toml`, validates the VisA shard split,
  runs configured candidates, and writes the matrix artifacts.
- `src/evm/core/torch_efficientnet.py` contains Torch/TorchVision training,
  evaluation, confusion matrix, GPU profile, environment report, model card,
  and MLflow logging helpers.
- `src/evm/control_panel/aggregation.py` now reads
  `F:/EnterpriseMLOps_Data/enterprise-vision-mlops/artifacts/w7/efficientnet/latest_model_matrix.json`
  when present and reflects real candidate status, run URIs, artifact URIs,
  metrics, and promotion blockers in `CycleRun.model_matrix`.
- `configs/w7_efficientnet_real_test.toml` now includes the VisA shard index,
  MLflow endpoint, and execution parameters required by the pipeline.

## Real Data Split Check

The real VisA shard index was checked without writing training artifacts:

```text
record_count=10821
train=6504
validation=2136
test=2181
blockers=[]
```

This means the next full training run is not blocked by split acceptance.

## Verification

```powershell
F:\evm_w7_torch\python.exe -m pytest tests\test_efficientnet_real_test_matrix.py tests\test_control_panel_aggregation.py tests\test_w7_real_test_policy.py -q
```

Result:

```text
9 passed in 0.22s
```

Full regression:

```powershell
F:\evm_w7_torch\python.exe -m pytest -q
```

Result:

```text
44 passed, 2 warnings in 1.78s
```

The warnings are existing FastAPI `on_event` deprecation warnings.

## Remaining Closure Evidence

`EVM-237` remains open until the following are produced by real training:

- EfficientNet-B0 candidate runs with at least 5 epochs.
- EfficientNet-B7 candidate runs with at least 3 epochs.
- One MLflow run per candidate.
- Candidate model artifacts under the W7 F-drive evidence root.
- Training history with epoch and optimizer-step counts.
- Confusion matrix JSON and PNG per candidate.
- GPU profile JSON per candidate.
- Environment report per candidate.
- Model card per candidate.
- `CycleRun.model_matrix` populated from actual candidate evidence.

## Next Command

The full acceptance command remains:

```powershell
F:\evm_w7_torch\python.exe scripts\run_pipeline.py efficientnet-training --config configs\w7_efficientnet_real_test.toml
```

This command is expected to be long-running and GPU-intensive, especially for
the EfficientNet-B7 candidates.
