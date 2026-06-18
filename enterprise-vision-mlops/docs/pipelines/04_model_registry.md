# Model Registry Pipeline

## Role

Versions the selected model and defines the promotion contract used by deployment.

## Current Local MVP Scope

- Reads `artifacts/models/vision-baseline/model.json`.
- Creates a versioned registry record.
- Writes `artifacts/registry/vision-baseline/latest.json`.

## Inputs

- `artifacts/models/vision-baseline/model.json`

## Outputs

- `artifacts/registry/vision-baseline/vN.json`
- `artifacts/registry/vision-baseline/latest.json`
- `artifacts/reports/model_registry.md`

## Command

```bash
python scripts/run_pipeline.py register-model --config configs/local.toml
```

## Extension Plan

- Replace local registry metadata with MLflow Model Registry promotion.
- Add approval gates and stage transitions.
- Add model card generation.
- Add rollback metadata and model lineage.

## Update Log

- 2026-06-18: Added local versioned registry metadata.
- 2026-06-18: Verified `vision-baseline` local registry version creation.
