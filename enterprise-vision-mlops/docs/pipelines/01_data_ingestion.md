# Data Ingestion Pipeline

## Role

Creates the raw dataset manifest that downstream validation and training stages consume.

## Current Local MVP Scope

- Generates a deterministic synthetic seed manifest.
- Writes `data/raw/raw_manifest.jsonl`.
- Emits a run summary under `artifacts/runs/data_ingestion`.
- Updates generated report `artifacts/reports/data_ingestion.md`.

## Inputs

- Config: `configs/local.toml`
- Future source options: public dataset, S3/MinIO bucket, Kafka/Redpanda event stream

## Outputs

- `data/raw/raw_manifest.jsonl`

## Command

```bash
python scripts/run_pipeline.py data-ingest --config configs/local.toml
```

## Extension Plan

- Add real dataset downloader.
- Add object storage upload to MinIO raw zone.
- Add event emission for ingested dataset versions.

## Update Log

- 2026-06-18: Added local MVP synthetic manifest generator.
- 2026-06-18: Verified manifest generation with 8 sample records.
