# Data Validation Pipeline

## Role

Validates data quality before any record is allowed to reach model training.

## Current Local MVP Scope

- Reads `data/raw/raw_manifest.jsonl`.
- Validates schema fields, image URI, object existence, extension, label, width, and height.
- Writes validated manifest, validation report, processed Parquet, validated Parquet, and dataset metadata.
- Uploads validated outputs to MinIO processed/validated buckets.
- Emits class distribution, dimension summary, schema summary, and failure reasons.

## Inputs

- `data/raw/raw_manifest.jsonl`

## Outputs

- `data/validated/validated_manifest.jsonl`
- `data/validated/validation_report.json`
- `data/processed/processed_dataset.parquet`
- `data/validated/validated_dataset.parquet`
- `data/validated/dataset_version.json`
- `artifacts/reports/data_validation.md`

## Command

```bash
python scripts/run_pipeline.py data-validate --config configs/local.toml
```

Airflow task command:

```bash
python scripts/run_pipeline.py data-validate --config configs/airflow.toml
```

## Extension Plan

- Replace manifest checks with Great Expectations.
- Add corrupted image detection.
- Add schema drift and class imbalance thresholds.
- Add dataset catalog table integration.

## Update Log

- 2026-06-18: Added manifest-level validation and report generation.
- 2026-06-18: Verified validation output with 8 valid records and 0 invalid records.
- 2026-06-21: Connected as Airflow task `data_validate` after `data_ingest`.
- 2026-07-01: Added MinIO object existence checks, Parquet generation, and dataset version metadata.
