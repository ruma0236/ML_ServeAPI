# Object Storage Bootstrap Pipeline

## Role

Ensures the local MinIO data platform has the buckets needed for enterprise-style
raw, processed, validated, and MLflow artifact zones.

## Current W2 Scope

- Ensures `raw`, `processed`, `validated`, and `mlflow-artifacts` buckets exist.
- Runs as the first Airflow task before data ingestion.
- Uses the same trace model as the rest of the pipeline.

## Inputs

- `[object_store]` and `[pipelines.object_storage_bootstrap]` in `configs/local.toml`
- MinIO endpoint from Docker Compose

## Outputs

- Required MinIO buckets exist.
- `artifacts/reports/object_storage_bootstrap.md`
- `artifacts/runs/object_storage_bootstrap/<run_id>/summary.json`

## Command

```bash
python scripts/run_pipeline.py object-store-bootstrap --config configs/local.toml
```

Airflow task command:

```bash
python scripts/run_pipeline.py object-store-bootstrap --config configs/airflow.toml
```

## Update Log

- 2026-07-01: Added W2 object storage bootstrap pipeline.
