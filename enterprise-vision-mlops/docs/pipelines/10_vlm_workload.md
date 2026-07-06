# VLM Workload Pipeline

## Purpose

The W4 VLM workload pipeline turns the existing control-plane foundation into a
manufacturing visual inspection MLOps path. It validates image data, creates
repeatable shards, runs a mock VLM adapter, validates structured output, blocks
bad candidates, writes audit/RCA events, and exports observability evidence.

## Storage Policy

Large local data and artifacts are stored outside the Git repository.

| Runtime | Data Root |
|---|---|
| Windows local | `F:\EnterpriseMLOps_Data\enterprise-vision-mlops` |
| Docker/Airflow | `/mnt/evm-data` |

MinIO is also mounted to the F-drive backed root through
`EVM_HOST_MINIO_ROOT`, so object storage data does not grow inside the repo.

## Command Order

```powershell
python scripts\run_pipeline.py dataset-intake-audit --config configs\local.toml
python scripts\run_pipeline.py domain-pack-check --config configs\local.toml
python scripts\run_pipeline.py object-store-bootstrap --config configs\local.toml
python scripts\run_pipeline.py data-ingest --config configs\local.toml
python scripts\run_pipeline.py data-validate --config configs\local.toml
python scripts\run_pipeline.py image-quality --config configs\local.toml
python scripts\run_pipeline.py dataset-shards --config configs\local.toml
python scripts\run_pipeline.py vlm-contract --config configs\local.toml
python scripts\run_pipeline.py vlm-batch-eval --config configs\local.toml
python scripts\run_pipeline.py vlm-reliability --config configs\local.toml
python scripts\run_pipeline.py vlm-rca --config configs\local.toml
python scripts\run_pipeline.py vlm-observability --config configs\local.toml
```

## Outputs

- `data/raw/industrial/source_registry.json`
- `data/raw/industrial/acquisition_plan.json`
- `data/raw/industrial/cleaning_benchmark.json`
- `data/raw/industrial/mvi_import_manifest.jsonl`
- `data/validated/mvi_quality_manifest.jsonl`
- `data/validated/mvi_quality_report.json`
- `data/validated/shards/shard_index.json`
- `artifacts/vlm/contracts/vlm_contract.json`
- `artifacts/vlm/latest_batch_summary.json`
- `artifacts/registry/vlm/prompt_registry.json`
- `artifacts/registry/vlm/model_registry.json`
- `artifacts/vlm/reliability/gate_report.json`
- `artifacts/vlm/audit/audit_events.jsonl`
- `artifacts/vlm/audit/failure_scenarios.json`
- `artifacts/vlm/observability/benchmark_report.json`
- `artifacts/vlm/observability/slo_report.md`
- `artifacts/vlm/observability/vlm_metrics.prom`

All paths above are under the configured F-drive backed root for local runs.

## Real Dataset Intake Status

`dataset-intake-audit` is the production-data entry point. It does not download
licensed datasets by itself. Instead, it scans the configured F-drive raw roots
from the manufacturing domain pack and writes:

- source/license/retention/access policy evidence,
- acquisition next actions and checkpoint locations,
- an import manifest when local images exist,
- exact-hash, dimension, brightness, blur, label, class, and split summaries.

When VisA or MVTec AD files are not present yet, the pipeline returns
`needs_data` and records `root_missing` for each dataset. That is a successful
control-plane audit state, not a claim that the real dataset has been validated.
