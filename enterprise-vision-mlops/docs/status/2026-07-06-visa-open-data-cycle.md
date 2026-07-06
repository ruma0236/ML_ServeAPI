# 2026-07-06 VisA Open Dataset Cycle Verification

## Summary

W4 was verified against the real VisA open dataset instead of only the local
fixture data. The dataset was downloaded from AWS Open Data to the F-drive raw
zone, extracted under the manufacturing visual inspection raw root, validated,
sharded, evaluated through the mock VLM workload, trained into the baseline
model path, registered, served, and monitored.

## Dataset Source

- Dataset: VisA
- Source: `https://registry.opendata.aws/visa/`
- Upstream project: `https://github.com/amazon-science/spot-diff`
- License: `CC-BY-4.0`
- Download artifact: `s3://amazon-visual-anomaly/VisA_20220922.tar`
- Local tar: `F:\EnterpriseMLOps_Data\enterprise-vision-mlops\data\raw\downloads\VisA_20220922.tar`
- Local extracted root: `F:\EnterpriseMLOps_Data\enterprise-vision-mlops\data\raw\industrial\visa`
- Tar size: `1929840640` bytes

## Execution

Primary full-cycle run:

```powershell
python scripts\run_pipeline.py dataset-intake-audit --config configs\local_visa.toml
python scripts\run_pipeline.py domain-pack-check --config configs\local_visa.toml
python scripts\run_pipeline.py object-store-bootstrap --config configs\local_visa.toml
python scripts\run_pipeline.py data-validate --config configs\local_visa.toml
python scripts\run_pipeline.py image-quality --config configs\local_visa.toml
python scripts\run_pipeline.py dataset-shards --config configs\local_visa.toml
python scripts\run_pipeline.py vlm-contract --config configs\local_visa.toml
python scripts\run_pipeline.py vlm-batch-eval --config configs\local_visa.toml
python scripts\run_pipeline.py vlm-reliability --config configs\local_visa.toml
python scripts\run_pipeline.py vlm-rca --config configs\local_visa.toml
python scripts\run_pipeline.py vlm-observability --config configs\local_visa.toml
python scripts\run_pipeline.py train --config configs\local_visa.toml
python scripts\run_pipeline.py register-model --config configs\local_visa.toml
python scripts\run_pipeline.py deploy-check --config configs\local_visa.toml
python scripts\run_pipeline.py monitor-check --config configs\local_visa.toml
```

Windows did not have `make` installed, so the same command sequence as the
`visa-open-data-cycle` target was executed directly.

## Observed Results

- Dataset intake: `status=ready_for_import`, `records_discovered=10821`.
- Data validation: `valid_records=10821`, `invalid_records=0`.
- Label counts: `normal=9621`, `anomaly=1200`.
- Dimension range: width `1274` to `1562`, height `960` to `1176`.
- Image quality: `status=pass`, `error_count=0`, `warning_count=0`,
  `duplicate_content_hashes=0`.
- Dataset shards: `record_count=10821`, `shard_count=23`.
- Split counts: `train=6504`, `validation=2136`, `test=2181`.
- VLM batch: `records=10821`, `schema_valid_rate=1.0`,
  `p95_latency_ms=8.933`, `error_types.none=10821`.
- Reliability gate: `promotion_decision=promote_candidate`,
  `blocking_failures=0`, `warning_failures=0`.
- RCA audit: `event_count=21649`, with `10821` request events and `10821`
  response events.
- Observability benchmark: `status=pass`.
- Training: `training_records=10821`, `baseline_accuracy=0.889105`,
  `mlflow_status=logged`.
- Registry: `vision-baseline` advanced to `version=5`, `stage=Production`.
- Deployment check: `/health`, `/ready`, and `/predict` returned HTTP `200`;
  `ready_model_loaded=true`, `predict_placeholder=false`, `contract_ok=true`.
- Monitoring check: Prometheus reported `healthy_targets=2`.

## Storage Verification

The final run used a Python 3.13 runtime with `pyarrow==18.1.0`, so the
processed and validated datasets were written as real Parquet files:

- `processed_dataset.parquet`: `1517797` bytes, magic bytes `PAR1`.
- `validated_dataset.parquet`: `1517264` bytes, magic bytes `PAR1`.

MinIO object verification:

- `s3://processed/visa-open-data/visa-open-data-f1f1c9ee9922/processed/processed_dataset.parquet`
  exists with content type `application/vnd.apache.parquet`.
- `s3://validated/visa-open-data/visa-open-data-f1f1c9ee9922/validated/validated_dataset.parquet`
  exists with content type `application/vnd.apache.parquet`.
- `s3://validated/visa-open-data/visa-open-data-f1f1c9ee9922/manifests/validated_manifest.jsonl`
  exists with content type `application/x-ndjson`.
- `s3://validated/visa-open-data/visa-open-data-f1f1c9ee9922/reports/validation_report.json`
  exists with content type `application/json`.
- `s3://validated/visa-open-data/visa-open-data-f1f1c9ee9922/metadata/dataset_version.json`
  exists with content type `application/json`.

## Key Artifacts

- `configs/local_visa.toml`
- `F:\EnterpriseMLOps_Data\enterprise-vision-mlops\data\raw\industrial\mvi_import_manifest.jsonl`
- `F:\EnterpriseMLOps_Data\enterprise-vision-mlops\data\validated\visa\validation_report.json`
- `F:\EnterpriseMLOps_Data\enterprise-vision-mlops\data\validated\visa\mvi_quality_report.json`
- `F:\EnterpriseMLOps_Data\enterprise-vision-mlops\data\validated\visa\shards\shard_index.json`
- `F:\EnterpriseMLOps_Data\enterprise-vision-mlops\artifacts\vlm\visa\latest_batch_summary.json`
- `F:\EnterpriseMLOps_Data\enterprise-vision-mlops\artifacts\vlm\visa\reliability\gate_report.json`
- `F:\EnterpriseMLOps_Data\enterprise-vision-mlops\artifacts\vlm\visa\audit\audit_summary.json`
- `F:\EnterpriseMLOps_Data\enterprise-vision-mlops\artifacts\vlm\visa\observability\benchmark_report.json`
- `F:\EnterpriseMLOps_Data\enterprise-vision-mlops\artifacts\registry\vision-baseline\v5.json`

## Remaining Boundary

This verifies the open-dataset control-plane cycle with the current mock VLM
adapter and majority-class baseline serving model. It does not yet verify a
real VLM model endpoint, GPU inference, HITL curation, or drift lifecycle
queues; those remain later model lifecycle and AgentOps work.
