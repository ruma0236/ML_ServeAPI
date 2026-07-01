# Data Ingestion Pipeline

## Role

Creates the raw dataset manifest that downstream validation and training stages consume.

## Current Local MVP Scope

- Generates deterministic public-vision seed records.
- Uploads sample raw image objects to MinIO raw bucket.
- Writes `data/raw/raw_manifest.jsonl`.
- Uploads the raw manifest to `s3://raw/public-vision-local/v1/manifests/raw_manifest.jsonl`.
- Emits a run summary under `artifacts/runs/data_ingestion`.
- Updates generated report `artifacts/reports/data_ingestion.md`.

## Inputs

- Config: `configs/local.toml`
- Config: `configs/local.toml`
- MinIO raw bucket from `[object_store].raw_bucket`
- Future source options: public dataset download, Kafka/Redpanda event stream

## Outputs

- `data/raw/raw_manifest.jsonl`
- `s3://raw/public-vision-local/v1/images/*.jpg`
- `s3://raw/public-vision-local/v1/manifests/raw_manifest.jsonl`

## Command

```bash
python scripts/run_pipeline.py data-ingest --config configs/local.toml
```

Airflow task command:

```bash
python scripts/run_pipeline.py data-ingest --config configs/airflow.toml
```

## Extension Plan

- Add real dataset downloader.
- Add event emission for ingested dataset versions.

## Update Log

- 2026-06-18: Added local MVP synthetic manifest generator.
- 2026-06-18: Verified manifest generation with 8 sample records.
- 2026-06-21: Connected as Airflow task `data_ingest` in `enterprise_vision_mlops_daily`.
- 2026-07-01: Added MinIO raw image and raw manifest upload for W2.
