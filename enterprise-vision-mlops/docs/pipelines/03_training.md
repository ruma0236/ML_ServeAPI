# Training Pipeline

## Role

Produces a reproducible model artifact and logs training metadata to MLflow.

## Current Local MVP Scope

- Reads `data/validated/validated_manifest.jsonl`.
- Trains a deterministic majority-class baseline.
- Writes model metadata to `artifacts/models/vision-baseline/model.json`.
- Logs params and metrics to MLflow through the REST API when MLflow is running.

## Inputs

- `data/validated/validated_manifest.jsonl`
- MLflow tracking server at `http://localhost:5000`

## Outputs

- `artifacts/models/vision-baseline/model.json`
- MLflow experiment run
- `artifacts/reports/training.md`

## Command

```bash
python scripts/run_pipeline.py train --config configs/local.toml
```

## Extension Plan

- Replace baseline with PyTorch training.
- Add config-based hyperparameters and seed control.
- Add checkpointing and model artifact upload.
- Add distributed training path for Ray/PyTorch DDP.

## Update Log

- 2026-06-18: Added baseline trainer and MLflow metric logging.
- 2026-06-18: Verified baseline artifact generation and MLflow metric logging.
