# Data Validation Pipeline

## Role

Validates data quality before any record is allowed to reach model training.

## Current Local MVP Scope

- Reads `data/raw/raw_manifest.jsonl`.
- Validates image URI, extension, label, width, and height.
- Writes validated manifest and validation report.
- Emits class distribution and failure reasons.

## Inputs

- `data/raw/raw_manifest.jsonl`

## Outputs

- `data/validated/validated_manifest.jsonl`
- `data/validated/validation_report.json`
- `artifacts/reports/data_validation.md`

## Command

```bash
python scripts/run_pipeline.py data-validate --config configs/local.toml
```

## Extension Plan

- Replace manifest checks with Great Expectations.
- Add corrupted image detection.
- Add schema drift and class imbalance thresholds.
- Store validation reports in object storage.

## Update Log

- 2026-06-18: Added manifest-level validation and report generation.
- 2026-06-18: Verified validation output with 8 valid records and 0 invalid records.
